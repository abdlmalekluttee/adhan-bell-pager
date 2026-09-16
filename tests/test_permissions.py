"""Unified RBAC: deny by default, module isolation, escalation guard, recovery account."""
import pytest
from conftest import admin_login, login, make_user
from fastapi.routing import APIRoute

from app import accounts, rbac
from app.deps import Authz
from app.main import PUBLIC_API, app


def test_every_api_route_declares_authorization():
    missing = []
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith("/api/"):
            continue
        if route.path in PUBLIC_API or route.path == "/api/{rest:path}":
            continue
        if not any(isinstance(d.call, Authz) for d in route.dependant.dependencies):
            missing.append(f"{sorted(route.methods)} {route.path}")
    assert not missing, "routes without Authz: " + ", ".join(missing)


def test_builtin_roles(env):
    roles = {r["key"]: r for r in rbac.list_roles() if r["key"]}
    assert set(roles) == {"full_admin", "adhan_admin", "bells_admin", "operator", "viewer"}
    assert set(roles["full_admin"]["permissions"]) == rbac.ALL_SET
    assert not any(p.startswith(("bells.", "system.")) for p in roles["adhan_admin"]["permissions"])
    assert not any(p.startswith(("adhan.", "system.")) for p in roles["bells_admin"]["permissions"])
    assert all(p.endswith(("view", "history.view")) for p in roles["viewer"]["permissions"])
    assert not any("schedule.edit" in p or "sounds" in p for p in roles["operator"]["permissions"])


def test_action_implies_view():
    assert rbac.normalize(["bells.broadcast"]) == ["bells.view", "bells.broadcast"]
    assert rbac.normalize(["nonsense", "system.audit.view"]) == ["system.audit.view"]


BELLS_ONLY_FORBIDDEN = [
    ("get", "/api/adhan/overview", None),
    ("put", "/api/adhan/settings", {"prayer": {"method": "EGYPTIAN"}}),
    ("post", "/api/adhan/test", {"prayer": "dhuhr"}),
    ("get", "/api/adhan/history", None),
    ("get", "/api/adhan/sounds", None),
    ("delete", "/api/adhan/sounds/x.wav", None),
    ("get", "/api/system/sip", None),
    ("put", "/api/system/sip", {"registrar": "evil"}),
    ("post", "/api/system/sip/reconnect", {}),
    ("put", "/api/system/destinations", {"destinations": [], "zones": []}),
    ("get", "/api/users", None),
    ("post", "/api/users", {"username": "x", "password": "Password-123!", "role_ids": [1]}),
    ("post", "/api/roles", {"name": "r", "permissions": ["system.users.manage"]}),
    ("get", "/api/directory", None),
    ("put", "/api/directory", {"enabled": True}),
    ("get", "/api/audit", None),
    ("get", "/api/system/logs", None),
    ("put", "/api/system/time", {"timezone": "UTC"}),
]


@pytest.mark.parametrize("method,path,body", BELLS_ONLY_FORBIDDEN)
def test_bells_admin_cannot_reach_other_modules(client, method, path, body):
    make_user("bellsboss", role_keys=["bells_admin"])
    h = login(client, "bellsboss", "Correct-Horse-9")
    kwargs = {"headers": h}
    if body is not None:
        kwargs["json"] = body
    r = getattr(client, method)(path, **kwargs)
    assert r.status_code == 403, (path, r.status_code, r.text)


ADHAN_ONLY_FORBIDDEN = [
    ("get", "/api/bells/day", None),
    ("post", "/api/bells/timetables", {"name": "x", "events": []}),
    ("post", "/api/bells/ring", {"sound": "Three beeps.wav"}),
    ("get", "/api/bells/history", None),
    ("delete", "/api/bells/sounds/Three beeps.wav", None),
    ("post", "/api/bells/exceptions", {"name": "h", "kind": "holiday", "date_from": "2026-01-01"}),
    ("get", "/api/users", None),
]


@pytest.mark.parametrize("method,path,body", ADHAN_ONLY_FORBIDDEN)
def test_adhan_admin_cannot_reach_bells(client, method, path, body):
    make_user("imam", role_keys=["adhan_admin"])
    h = login(client, "imam", "Correct-Horse-9")
    kwargs = {"headers": h}
    if body is not None:
        kwargs["json"] = body
    assert getattr(client, method)(path, **kwargs).status_code == 403


def test_module_admins_can_use_their_module(client):
    make_user("bellsboss", role_keys=["bells_admin"])
    h = login(client, "bellsboss", "Correct-Horse-9")
    assert client.get("/api/bells/day").status_code == 200
    r = client.post("/api/bells/timetables", json={"name": "Day", "events": [
        {"label": "P1", "kind": "period", "start_time": "08:00", "end_time": "08:40"}]}, headers=h)
    assert r.status_code == 200, r.text
    me = client.get("/api/auth/me").json()
    assert me["modules"] == ["bells"]
    dash = client.get("/api/dashboard").json()
    assert dash["modules"] == ["bells"]


def test_viewer_is_read_only(client):
    make_user("watcher", role_keys=["viewer"])
    h = login(client, "watcher", "Correct-Horse-9")
    assert client.get("/api/bells/day").status_code == 200
    assert client.get("/api/adhan/overview").status_code == 200
    assert client.post("/api/bells/ring", json={"sound": "Three beeps.wav"}, headers=h).status_code == 403
    assert client.post("/api/broadcast/stop", headers=h).status_code == 403
    assert client.post("/api/bells/timetables", json={"name": "x", "events": []}, headers=h).status_code == 403


def test_operator_can_ring_but_not_edit(client):
    make_user("operator1", role_keys=["operator"])
    h = login(client, "operator1", "Correct-Horse-9")
    assert client.post("/api/bells/ring", json={"sound": "Three beeps.wav"}, headers=h).status_code == 200
    assert client.post("/api/bells/timetables", json={"name": "x", "events": []}, headers=h).status_code == 403


def test_bells_operator_cannot_interrupt_adhan(client, env):
    """A bells-only operator may not force a manual bell over an Adhan they cannot stop."""
    make_user("bellop", perms=["bells.broadcast", "bells.broadcast.stop"])
    h = login(client, "bellop", "Correct-Horse-9")
    from app import dispatcher

    class Busy:
        def active_module(self):
            return "adhan"
    real = dispatcher.get()
    dispatcher.set_dispatcher(Busy())
    try:
        r = client.post("/api/bells/ring", json={"sound": "Three beeps.wav", "force": True}, headers=h)
    finally:
        dispatcher.set_dispatcher(real)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "cannot_interrupt"


def test_escalation_guard_on_roles_and_users(client):
    # A user manager without system settings cannot grant it, nor edit a full admin.
    make_user("hradmin", perms=["system.users.manage", "system.roles.manage", "bells.view"])
    h = login(client, "hradmin", "Correct-Horse-9")
    fa = rbac.role_id_by_key("full_admin")
    r = client.post("/api/users", json={"username": "newbie", "password": "Password-123!", "role_ids": [fa]}, headers=h)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "escalation"
    r = client.post("/api/roles", json={"name": "Sneaky", "permissions": ["system.settings.manage"]}, headers=h)
    assert r.status_code == 403
    admin = accounts.get_by_username("admin")
    r = client.put(f"/api/users/{admin['id']}", json={"disabled": True}, headers=h)
    assert r.status_code == 403
    viewer = rbac.role_id_by_key("viewer")
    r = client.post("/api/users", json={"username": "okuser", "password": "Password-123!", "role_ids": []}, headers=h)
    assert r.status_code == 200
    r = client.put(f"/api/users/{r.json()['id']}", json={"role_ids": [viewer]}, headers=h)
    assert r.status_code == 403        # viewer includes adhan.view, which hr does not hold
    r = client.post("/api/roles", json={"name": "Bell watcher", "permissions": ["bells.view"]}, headers=h)
    assert r.status_code == 200


def test_builtin_roles_are_immutable(client):
    h = admin_login(client)
    fa = rbac.role_id_by_key("viewer")
    assert client.put(f"/api/roles/{fa}", json={"name": "x", "permissions": ["bells.view"]}, headers=h).status_code == 400
    assert client.delete(f"/api/roles/{fa}", headers=h).status_code == 400


def test_recovery_account_protected_and_last_admin(client):
    h = admin_login(client)
    admin = accounts.get_by_username("admin")
    assert client.delete(f"/api/users/{admin['id']}", headers=h).status_code == 400
    assert client.put(f"/api/users/{admin['id']}", json={"disabled": True}, headers=h).status_code == 400
    other = make_user("second", role_keys=["full_admin"])
    r = client.put(f"/api/users/{other['id']}", json={"role_ids": [rbac.role_id_by_key("viewer")]}, headers=h)
    assert r.status_code == 200
    eff = rbac.effective_for_user(accounts.get_by_username("admin"))
    assert eff.is_full_admin


def test_effective_permissions_show_sources(client):
    u = make_user("mixed", role_keys=["viewer", "bells_admin"])
    eff = rbac.effective_for_user(u)
    assert "Viewer (assigned)" in eff.sources["bells.view"]
    assert "School Bells Administrator (assigned)" in eff.sources["bells.view"]
    assert eff.sources["bells.schedule.edit"] == ["School Bells Administrator (assigned)"]


def test_timezone_change_via_location_needs_system_permission(client):
    make_user("imam", role_keys=["adhan_admin"])
    h = login(client, "imam", "Correct-Horse-9")
    r = client.put("/api/adhan/location", json={"use_manual": True, "latitude": 51.5, "longitude": 0,
                                                "timezone": "Europe/London"}, headers=h)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "timezone_permission"
    r = client.put("/api/adhan/location", json={"use_manual": True, "latitude": 32.9, "longitude": 13.2,
                                                "timezone": "Africa/Tripoli"}, headers=h)
    assert r.status_code == 200


def test_secrets_never_returned(client):
    h = admin_login(client)
    r = client.put("/api/system/sip", json={"registrar": "192.0.2.10", "username": "3001", "auth_user": "3001",
                                            "password": "super-secret-pw"}, headers=h)
    assert r.status_code == 200
    assert "super-secret-pw" not in r.text
    assert "super-secret-pw" not in client.get("/api/system/sip").text
    # blank password keeps the stored one
    client.put("/api/system/sip", json={"display_name": "Bells", "password": ""}, headers=h)
    from app import config
    assert config.get().sip.password == "super-secret-pw"
    audit_text = client.get("/api/audit").text
    assert "super-secret-pw" not in audit_text


def test_refused_last_admin_change_writes_nothing(client):
    h = admin_login(client)
    admin = accounts.get_by_username("admin")
    from app import db
    db.execute("UPDATE users SET is_recovery = 0 WHERE id = ?", (admin["id"],))   # simulate a non-recovery sole admin
    other = make_user("second", role_keys=["viewer"])
    login(client, "admin", "Admin-Pass-123")
    h = {"x-csrf-token": client.cookies.get(accounts.CSRF_COOKIE)}
    r = client.put(f"/api/users/{admin['id']}", headers=h,
                   json={"display_name": "Changed", "role_ids": [rbac.role_id_by_key("viewer")]})
    assert r.status_code in (400, 403)
    after = accounts.get_user(admin["id"])
    assert after["display_name"] != "Changed"
    assert rbac.effective_for_user(after).is_full_admin
    assert other


def test_escalation_error_lists_missing_permissions_separately(client):
    make_user("hradmin2", perms=["system.users.manage", "bells.view"])
    h = login(client, "hradmin2", "Correct-Horse-9")
    fa = rbac.role_id_by_key("full_admin")
    r = client.post("/api/users", json={"username": "newbie2", "password": "Password-123!", "role_ids": [fa]}, headers=h)
    d = r.json()["detail"]
    assert r.status_code == 403 and d["message"] == "You cannot grant permissions you do not have yourself."
    assert "system.settings.manage" in d["missing"]


def test_zone_used_by_adhan_cannot_be_removed(client):
    h = admin_login(client)
    client.put("/api/system/destinations", headers=h, json={
        "destinations": [{"id": "d1", "name": "Hall", "uri": "4090", "enabled": True}],
        "zones": [{"id": "z1", "name": "Hall zone", "destination_ids": ["d1"]}]})
    assert client.put("/api/adhan/settings", headers=h, json={"zone_ids": ["z1"]}).status_code == 200
    r = client.put("/api/system/destinations", headers=h, json={
        "destinations": [{"id": "d1", "name": "Hall", "uri": "4090", "enabled": True}], "zones": []})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "zone_in_use"
