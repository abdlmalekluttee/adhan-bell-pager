"""Role-based access control.

Model
-----
* A **permission** is a string such as ``bells.schedule.edit``. The catalog below
  is the complete list; anything not in it is ignored (deny by default).
* A **role** is a named set of permissions. Five built-in roles ship with the
  app; their permissions are defined in code so new permissions added in later
  versions reach them automatically. Built-in roles cannot be edited or deleted.
* A user's **effective permissions** are the union of every role they hold,
  whether assigned directly (local assignment) or granted through a mapped
  Active Directory group. There are no deny rules: roles only ever add.
* Every action permission implies the module's ``view`` permission.

Escalation guard
----------------
An administrator may only grant, map, or build roles out of permissions they
hold themselves, and may only edit users whose permissions are a subset of
their own. Only holders of every permission (Full Administrators) can manage
other Full Administrators.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import db

# (key, module, label, description)
CATALOG: list[tuple[str, str, str, str]] = [
    ("adhan.view", "adhan", "View Adhan", "See prayer times, the Adhan schedule and status."),
    ("adhan.schedule.edit", "adhan", "Edit Adhan schedule",
     "Change calculation, prayers, iqama, location, timetable import and Adhan zones."),
    ("adhan.sounds.manage", "adhan", "Manage Adhan sounds", "Upload, assign and delete Adhan audio."),
    ("adhan.broadcast", "adhan", "Start and test Adhan", "Play a test Adhan or a file right now."),
    ("adhan.broadcast.stop", "adhan", "Stop Adhan", "Stop an Adhan broadcast that is playing."),
    ("adhan.history.view", "adhan", "View Adhan history", "See past Adhan broadcasts and failures."),
    ("bells.view", "bells", "View School Bells", "See today's bells, timetables and schedules."),
    ("bells.schedule.edit", "bells", "Edit bell schedules",
     "Create, edit and delete timetables, schedules, holidays and overrides."),
    ("bells.sounds.manage", "bells", "Manage bell sounds", "Upload and delete bell sounds."),
    ("bells.broadcast", "bells", "Ring and test bells", "Ring a bell now or play a test."),
    ("bells.broadcast.stop", "bells", "Stop bells", "Stop a bell that is ringing."),
    ("bells.history.view", "bells", "View bell history", "See past bells and failures."),
    ("system.destinations.manage", "system", "Manage destinations and zones",
     "Add and edit the extensions, paging groups and zones both modules use."),
    ("system.sip.manage", "system", "Configure SIP", "SIP account, codecs, call behaviour, reconnect."),
    ("system.settings.manage", "system", "System settings",
     "Timezone, time sync and the broadcast conflict policy."),
    ("system.directory.manage", "system", "Configure Active Directory",
     "Directory connection and group-to-role mappings."),
    ("system.users.manage", "system", "Manage users", "Create, edit, disable users and assign roles."),
    ("system.roles.manage", "system", "Manage roles", "Create and edit custom roles."),
    ("system.audit.view", "system", "View audit log", "See who changed what."),
    ("system.logs.view", "system", "View service logs", "Technical service logs for troubleshooting."),
]

ALL = [c[0] for c in CATALOG]
ALL_SET = frozenset(ALL)
MODULES = ("adhan", "bells", "system")


def _mod(module: str, *actions: str) -> list[str]:
    return [f"{module}.{a}" for a in actions]


BUILTIN_ROLES: dict[str, dict] = {
    "full_admin": {
        "name": "Full Administrator",
        "description": "Everything: both modules, users, roles, directory and system settings.",
        "permissions": ALL,
    },
    "adhan_admin": {
        "name": "Adhan Administrator",
        "description": "Manage the Adhan module. No school bells or security administration.",
        "permissions": _mod("adhan", "view", "schedule.edit", "sounds.manage", "broadcast",
                            "broadcast.stop", "history.view"),
    },
    "bells_admin": {
        "name": "School Bells Administrator",
        "description": "Manage school timetables and bells. No Adhan or security administration.",
        "permissions": _mod("bells", "view", "schedule.edit", "sounds.manage", "broadcast",
                            "broadcast.stop", "history.view"),
    },
    "operator": {
        "name": "Operator",
        "description": "Day-to-day actions in both modules: view, ring or test, stop, see history.",
        "permissions": _mod("adhan", "view", "broadcast", "broadcast.stop", "history.view")
        + _mod("bells", "view", "broadcast", "broadcast.stop", "history.view"),
    },
    "viewer": {
        "name": "Viewer",
        "description": "Read-only access to both modules.",
        "permissions": _mod("adhan", "view", "history.view") + _mod("bells", "view", "history.view"),
    },
}


def normalize(perms) -> list[str]:
    """Keep known permissions only, add implied module view, stable catalog order."""
    s = {p for p in (perms or []) if p in ALL_SET}
    for p in list(s):
        module = p.split(".", 1)[0]
        if module in ("adhan", "bells"):
            s.add(f"{module}.view")
    return [p for p in ALL if p in s]


def seed_builtin_roles() -> None:
    now = db.now()
    for key, spec in BUILTIN_ROLES.items():
        row = db.one("SELECT id FROM roles WHERE key = ?", (key,))
        if row:
            db.execute("UPDATE roles SET builtin = 1, updated_at = ? WHERE id = ?", (now, row["id"]))
            continue
        name = spec["name"]
        if db.one("SELECT id FROM roles WHERE name = ?", (name,)):
            name = f"{name} (built-in)"
        db.execute(
            "INSERT INTO roles (key, name, description, builtin, permissions, created_at, updated_at) "
            "VALUES (?, ?, ?, 1, '[]', ?, ?)",
            (key, name, spec["description"], now, now),
        )


def role_permissions(role: dict) -> list[str]:
    if role.get("builtin") and role.get("key") in BUILTIN_ROLES:
        return normalize(BUILTIN_ROLES[role["key"]]["permissions"])
    return normalize(db.loads(role.get("permissions"), []))


def list_roles() -> list[dict]:
    rows = db.query("SELECT * FROM roles ORDER BY builtin DESC, name")
    counts = {r["role_id"]: r["n"] for r in db.query(
        "SELECT role_id, COUNT(*) AS n FROM user_roles GROUP BY role_id")}
    maps = {r["role_id"]: r["n"] for r in db.query(
        "SELECT role_id, COUNT(*) AS n FROM ad_group_mappings GROUP BY role_id")}
    out = []
    for r in rows:
        out.append({
            "id": r["id"], "key": r["key"], "name": r["name"], "description": r["description"],
            "builtin": bool(r["builtin"]), "permissions": role_permissions(r),
            "user_count": counts.get(r["id"], 0), "mapping_count": maps.get(r["id"], 0),
        })
    return out


def get_role(role_id: int) -> dict | None:
    r = db.one("SELECT * FROM roles WHERE id = ?", (role_id,))
    if not r:
        return None
    return {"id": r["id"], "key": r["key"], "name": r["name"], "description": r["description"],
            "builtin": bool(r["builtin"]), "permissions": role_permissions(r)}


def role_id_by_key(key: str) -> int | None:
    r = db.one("SELECT id FROM roles WHERE key = ?", (key,))
    return r["id"] if r else None


@dataclass
class Effective:
    permissions: list[str]
    sources: dict[str, list[str]] = field(default_factory=dict)   # perm -> ["Role (direct)", ...]
    roles: list[dict] = field(default_factory=list)                # [{id,name,via}]

    def has(self, perm: str) -> bool:
        return perm in self.permissions

    @property
    def is_full_admin(self) -> bool:
        return ALL_SET.issubset(self.permissions)

    def modules(self) -> list[str]:
        return [m for m in ("adhan", "bells") if f"{m}.view" in self.permissions] + (
            ["system"] if any(p.startswith("system.") for p in self.permissions) else [])


def effective_for_user(user: dict) -> Effective:
    """Union of direct roles and AD-mapped roles, with provenance."""
    direct = db.query(
        "SELECT r.* FROM roles r JOIN user_roles ur ON ur.role_id = r.id WHERE ur.user_id = ?",
        (user["id"],),
    )
    via: list[tuple[dict, str]] = [(r, "direct") for r in direct]
    if user.get("source") == "ad":
        # Group membership comes from the last directory check; mappings are read
        # live so removing a mapping takes effect immediately.
        member_of = {str(g).lower() for g in db.loads(user.get("ad_groups"), [])}
        by_role: dict[int, list[str]] = {}
        for m in db.query("SELECT role_id, group_name, group_dn FROM ad_group_mappings"):
            if m["group_dn"].lower() in member_of:
                by_role.setdefault(m["role_id"], []).append(m["group_name"] or m["group_dn"])
        if by_role:
            ids = list(by_role)
            marks = ",".join("?" * len(ids))
            for r in db.query(f"SELECT * FROM roles WHERE id IN ({marks})", ids):
                via.append((r, "ad:" + ", ".join(by_role[r["id"]])))
    if user.get("is_recovery"):
        fa = db.one("SELECT * FROM roles WHERE key = 'full_admin'")
        if fa and all(r["id"] != fa["id"] for r, _ in via):
            via.append((fa, "recovery"))
    perms: set[str] = set()
    sources: dict[str, list[str]] = {}
    roles = []
    for r, how in via:
        rp = role_permissions(r)
        label = _source_label(r["name"], how)
        roles.append({"id": r["id"], "name": r["name"], "via": how, "label": label})
        for p in rp:
            perms.add(p)
            sources.setdefault(p, []).append(label)
    return Effective(permissions=normalize(perms), sources=sources, roles=roles)


def _source_label(role_name: str, how: str) -> str:
    if how == "direct":
        return f"{role_name} (assigned)"
    if how == "recovery":
        return f"{role_name} (recovery account)"
    if how.startswith("ad:"):
        return f"{role_name} (AD group: {how[3:]})"
    return role_name


# --------------------------------------------------------------------------- #
# Escalation guard
# --------------------------------------------------------------------------- #
class Forbidden(Exception):
    def __init__(self, message: str, missing: list[str] | None = None):
        super().__init__(message)
        self.missing = missing or []


def ensure_can_grant(actor: Effective, perms) -> None:
    missing = [p for p in normalize(perms) if not actor.has(p)]
    if missing:
        raise Forbidden("You cannot grant permissions you do not have yourself.", missing)


def ensure_can_manage_user(actor: Effective, target_user: dict) -> None:
    target = effective_for_user(target_user)
    extra = [p for p in target.permissions if not actor.has(p)]
    if extra:
        raise Forbidden("This user has permissions you do not have, so you cannot change them.")


def validate_custom_role(name: str, permissions) -> tuple[str, list[str]]:
    name = (name or "").strip()
    if not name or len(name) > 60:
        raise ValueError("Role name is required (up to 60 characters).")
    perms = normalize(permissions)
    unknown = [p for p in (permissions or []) if p not in ALL_SET]
    if unknown:
        raise ValueError("Unknown permissions: " + ", ".join(unknown))
    if not perms:
        raise ValueError("Choose at least one permission.")
    return name, perms


def catalog() -> list[dict]:
    return [{"key": k, "module": m, "label": label, "description": d} for k, m, label, d in CATALOG]
