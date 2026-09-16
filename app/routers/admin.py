"""Users, roles, Active Directory settings and mappings, audit log."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from .. import accounts, audit, directory, rbac
from ..deps import Authz, Principal, bad_request, not_found

router = APIRouter(prefix="/api", tags=["admin"])


def _forbidden(message, missing: list[str] | None = None):
    from fastapi import HTTPException
    if isinstance(message, rbac.Forbidden):
        missing = message.missing
        message = str(message)
    return HTTPException(403, detail={"code": "escalation", "message": message, "missing": missing or []})


# --------------------------------------------------------------------------- #
# Permission catalog & roles
# --------------------------------------------------------------------------- #
@router.get("/permissions", dependencies=[Depends(Authz(any_of=["system.users.manage", "system.roles.manage",
                                                               "system.directory.manage"]))])
def permission_catalog():
    return {"permissions": rbac.catalog(), "modules": list(rbac.MODULES)}


@router.get("/roles", dependencies=[Depends(Authz(any_of=["system.users.manage", "system.roles.manage",
                                                         "system.directory.manage"]))])
def list_roles():
    return {"roles": rbac.list_roles()}


class RoleIn(BaseModel):
    name: str = Field("", max_length=60)
    description: str = Field("", max_length=300)
    permissions: list[str] = Field(default_factory=list)


@router.post("/roles", dependencies=[Depends(Authz("system.roles.manage"))])
def create_role(body: RoleIn, request: Request):
    p: Principal = request.state.principal
    try:
        name, perms = rbac.validate_custom_role(body.name, body.permissions)
        rbac.ensure_can_grant(p.effective, perms)
    except ValueError as e:
        raise bad_request(str(e))
    except rbac.Forbidden as e:
        raise _forbidden(e)
    from .. import db
    if db.one("SELECT id FROM roles WHERE name = ?", (name,)):
        raise bad_request("A role with that name already exists.", "duplicate")
    now = db.now()
    rid = db.execute("INSERT INTO roles (name, description, builtin, permissions, created_at, updated_at) "
                     "VALUES (?, ?, 0, ?, ?, ?)", (name, body.description.strip(), db.dumps(perms), now, now)).lastrowid
    audit.from_request(request, "role.create", target=name, details={"permissions": perms})
    return rbac.get_role(rid)


@router.put("/roles/{role_id}", dependencies=[Depends(Authz("system.roles.manage"))])
def update_role(role_id: int, body: RoleIn, request: Request):
    p: Principal = request.state.principal
    role = rbac.get_role(role_id)
    if not role:
        raise not_found("Role not found.")
    if role["builtin"]:
        raise bad_request("Built-in roles cannot be changed. Create a custom role instead.", "builtin")
    try:
        name, perms = rbac.validate_custom_role(body.name, body.permissions)
        # Both what is added and what the role already grants must be within the actor's own permissions.
        rbac.ensure_can_grant(p.effective, set(perms) | set(role["permissions"]))
    except ValueError as e:
        raise bad_request(str(e))
    except rbac.Forbidden as e:
        raise _forbidden(e)
    from .. import db
    if db.one("SELECT id FROM roles WHERE name = ? AND id <> ?", (name, role_id)):
        raise bad_request("A role with that name already exists.", "duplicate")
    before = role["permissions"]
    db.execute("UPDATE roles SET name = ?, description = ?, permissions = ?, updated_at = ? WHERE id = ?",
               (name, body.description.strip(), db.dumps(perms), db.now(), role_id))
    audit.from_request(request, "role.update", target=name, details={
        "added": sorted(set(perms) - set(before)), "removed": sorted(set(before) - set(perms))})
    return rbac.get_role(role_id)


@router.delete("/roles/{role_id}", dependencies=[Depends(Authz("system.roles.manage"))])
def delete_role(role_id: int, request: Request):
    p: Principal = request.state.principal
    role = rbac.get_role(role_id)
    if not role:
        raise not_found("Role not found.")
    if role["builtin"]:
        raise bad_request("Built-in roles cannot be deleted.", "builtin")
    try:
        rbac.ensure_can_grant(p.effective, role["permissions"])
    except rbac.Forbidden as e:
        raise _forbidden(e)
    from .. import db
    db.execute("DELETE FROM roles WHERE id = ?", (role_id,))
    audit.from_request(request, "role.delete", target=role["name"])
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Users
# --------------------------------------------------------------------------- #
@router.get("/users", dependencies=[Depends(Authz("system.users.manage"))])
def list_users():
    return {"users": accounts.list_users()}


@router.get("/users/{user_id}", dependencies=[Depends(Authz("system.users.manage"))])
def get_user(user_id: int):
    u = accounts.get_user(user_id)
    if not u:
        raise not_found("User not found.")
    return accounts.public_user(u, with_effective=True)


class UserCreate(BaseModel):
    username: str = Field("", max_length=64)
    display_name: str = Field("", max_length=120)
    email: str = Field("", max_length=200)
    password: str = Field("", max_length=512)
    must_change_password: bool = True
    role_ids: list[int] = Field(default_factory=list)


def _roles_perms(role_ids: list[int]) -> list[str]:
    perms: set[str] = set()
    for rid in role_ids:
        r = rbac.get_role(rid)
        if not r:
            raise bad_request("One of the selected roles no longer exists.")
        perms.update(r["permissions"])
    return sorted(perms)


@router.post("/users", dependencies=[Depends(Authz("system.users.manage"))])
def create_user(body: UserCreate, request: Request):
    p: Principal = request.state.principal
    try:
        rbac.ensure_can_grant(p.effective, _roles_perms(body.role_ids))
    except rbac.Forbidden as e:
        raise _forbidden(e)
    try:
        u = accounts.create_user(body.username.strip(), password=body.password, display_name=body.display_name,
                                 email=body.email, must_change_password=body.must_change_password,
                                 role_ids=body.role_ids)
    except ValueError as e:
        raise bad_request(str(e))
    audit.from_request(request, "user.create", target=u["username"], details={"role_ids": body.role_ids})
    return accounts.public_user(u, with_effective=True)


class UserUpdate(BaseModel):
    display_name: str | None = Field(None, max_length=120)
    email: str | None = Field(None, max_length=200)
    disabled: bool | None = None
    role_ids: list[int] | None = None


@router.put("/users/{user_id}", dependencies=[Depends(Authz("system.users.manage"))])
def update_user(user_id: int, body: UserUpdate, request: Request):
    p: Principal = request.state.principal
    u = accounts.get_user(user_id)
    if not u:
        raise not_found("User not found.")
    try:
        rbac.ensure_can_manage_user(p.effective, u)
        if body.role_ids is not None:
            rbac.ensure_can_grant(p.effective, _roles_perms(body.role_ids))
    except rbac.Forbidden as e:
        raise _forbidden(e)
    if u["is_recovery"] and (body.disabled or (body.role_ids is not None and
                                               rbac.role_id_by_key("full_admin") not in body.role_ids)):
        raise bad_request("The recovery administrator cannot be disabled or lose Full Administrator.",
                          "recovery_account")
    if user_id == p.id and body.disabled:
        raise bad_request("You cannot disable your own account.", "self")
    before = accounts.public_user(u, with_effective=True)
    if _would_remove_last_full_admin(u, before, body):
        raise bad_request("This change would leave no enabled Full Administrator.", "last_admin")
    if body.role_ids is not None:
        accounts.set_roles(user_id, body.role_ids)
    accounts.update_user(user_id, display_name=body.display_name, email=body.email, disabled=body.disabled)
    after = accounts.public_user(accounts.get_user(user_id), with_effective=True)
    audit.from_request(request, "user.update", target=u["username"], details={
        "role_ids": {"before": before["role_ids"], "after": after["role_ids"]},
        "disabled": {"before": before["disabled"], "after": after["disabled"]},
        "permissions_added": sorted(set(after["permissions"]) - set(before["permissions"])),
        "permissions_removed": sorted(set(before["permissions"]) - set(after["permissions"]))})
    return after


def _would_remove_last_full_admin(u: dict, before: dict, body: UserUpdate) -> bool:
    """Checked before anything is written, so a refused change leaves the user untouched."""
    if not rbac.ALL_SET.issubset(before["permissions"]) or before["disabled"]:
        return False
    disabled_after = before["disabled"] if body.disabled is None else body.disabled
    perms_after: set[str] = set()
    if not disabled_after:
        if u["is_recovery"]:
            return False
        role_ids = before["role_ids"] if body.role_ids is None else body.role_ids
        perms_after.update(_roles_perms(role_ids))
        for r in before["roles"]:
            if r["via"].startswith("ad:"):
                perms_after.update(rbac.get_role(r["id"])["permissions"])
    if rbac.ALL_SET.issubset(perms_after):
        return False
    return accounts.full_admin_count(exclude_user_id=u["id"]) == 0


class ResetPasswordIn(BaseModel):
    password: str = Field("", max_length=512)
    must_change_password: bool = True


@router.post("/users/{user_id}/reset-password", dependencies=[Depends(Authz("system.users.manage"))])
def reset_password(user_id: int, body: ResetPasswordIn, request: Request):
    p: Principal = request.state.principal
    u = accounts.get_user(user_id)
    if not u:
        raise not_found("User not found.")
    if u["source"] != "local":
        raise bad_request("Directory users reset their password in Active Directory.", "not_local")
    try:
        rbac.ensure_can_manage_user(p.effective, u)
        accounts.set_password(user_id, body.password, must_change=body.must_change_password)
    except rbac.Forbidden as e:
        raise _forbidden(e)
    except ValueError as e:
        raise bad_request(str(e), "weak_password")
    n = accounts.revoke_sessions(user_id)
    audit.from_request(request, "user.reset_password", target=u["username"], details={"sessions_ended": n})
    return {"ok": True}


@router.delete("/users/{user_id}", dependencies=[Depends(Authz("system.users.manage"))])
def delete_user(user_id: int, request: Request):
    p: Principal = request.state.principal
    u = accounts.get_user(user_id)
    if not u:
        raise not_found("User not found.")
    if u["is_recovery"]:
        raise bad_request("The recovery administrator cannot be deleted.", "recovery_account")
    if user_id == p.id:
        raise bad_request("You cannot delete your own account.", "self")
    try:
        rbac.ensure_can_manage_user(p.effective, u)
    except rbac.Forbidden as e:
        raise _forbidden(e)
    if accounts.full_admin_count(exclude_user_id=user_id) == 0:
        raise bad_request("This would leave no enabled Full Administrator.", "last_admin")
    accounts.delete_user(user_id)
    audit.from_request(request, "user.delete", target=u["username"])
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Active Directory
# --------------------------------------------------------------------------- #
@router.get("/directory", dependencies=[Depends(Authz("system.directory.manage"))])
def get_directory():
    return {"config": directory.public_config(), "mappings": directory.list_mappings()}


@router.put("/directory", dependencies=[Depends(Authz("system.directory.manage"))])
def save_directory(body: dict, request: Request):
    before = directory.public_config()
    try:
        cfg = directory.save_config(body or {})
    except ValueError as e:
        audit.from_request(request, "directory.update", result="error", details={"error": str(e)})
        raise bad_request(str(e))
    changed = sorted(k for k in cfg if cfg.get(k) != before.get(k))
    if body.get("bind_password"):
        changed.append("bind_password")
    audit.from_request(request, "directory.update", details={"changed": changed, "enabled": cfg["enabled"]})
    return {"config": cfg}


@router.post("/directory/test", dependencies=[Depends(Authz("system.directory.manage"))])
def test_directory(request: Request, body: dict | None = None):
    try:
        res = directory.test_connection(body or None)
    except ValueError as e:
        raise bad_request(str(e))
    audit.from_request(request, "directory.test_connection", result="ok" if res.get("ok") else "error",
                       details={"code": res.get("code", "")})
    return res


class LookupIn(BaseModel):
    username: str = Field("", max_length=200)
    password: str = Field("", max_length=512)


@router.post("/directory/lookup-user", dependencies=[Depends(Authz("system.directory.manage"))])
def lookup_user(body: LookupIn, request: Request):
    res = directory.lookup_user(body.username, body.password)
    audit.from_request(request, "directory.lookup_user", target=body.username[:64],
                       result="ok" if res.get("ok") else "error")
    return res


@router.get("/directory/groups", dependencies=[Depends(Authz("system.directory.manage"))])
def search_groups(q: str = ""):
    return directory.search_groups(q[:100])


class MappingIn(BaseModel):
    group_dn: str = Field("", max_length=500)
    group_name: str = Field("", max_length=120)
    role_id: int


@router.post("/directory/mappings", dependencies=[Depends(Authz("system.directory.manage"))])
def add_mapping(body: MappingIn, request: Request):
    p: Principal = request.state.principal
    role = rbac.get_role(body.role_id)
    if not role:
        raise bad_request("Role not found.")
    try:
        rbac.ensure_can_grant(p.effective, role["permissions"])
        m = directory.add_mapping(body.group_dn, body.group_name, body.role_id)
    except rbac.Forbidden as e:
        raise _forbidden(e)
    except ValueError as e:
        raise bad_request(str(e))
    audit.from_request(request, "directory.mapping_add", target=m["group_dn"], details={"role": role["name"]})
    return m


@router.delete("/directory/mappings/{mapping_id}", dependencies=[Depends(Authz("system.directory.manage"))])
def delete_mapping(mapping_id: int, request: Request):
    p: Principal = request.state.principal
    from .. import db
    m = db.one("SELECT * FROM ad_group_mappings WHERE id = ?", (mapping_id,))
    if not m:
        raise not_found("Mapping not found.")
    role = rbac.get_role(m["role_id"])
    try:
        rbac.ensure_can_grant(p.effective, role["permissions"] if role else [])
    except rbac.Forbidden as e:
        raise _forbidden(e)
    directory.delete_mapping(mapping_id)
    audit.from_request(request, "directory.mapping_remove", target=m["group_dn"],
                       details={"role": role["name"] if role else m["role_id"]})
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Audit
# --------------------------------------------------------------------------- #
@router.get("/audit", dependencies=[Depends(Authz("system.audit.view"))])
def audit_log(limit: int = 100, offset: int = 0, action: str = "", actor: str = "", result: str = ""):
    return audit.search(limit=limit, offset=offset, action=action[:60], actor=actor[:64], result=result[:20])
