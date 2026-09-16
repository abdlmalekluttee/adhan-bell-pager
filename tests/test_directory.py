"""Active Directory integration against an in-memory LDAP directory (ldap3 MOCK_SYNC).

These tests exercise the real search, bind, nested-group, mapping, refresh and
outage logic. What they cannot prove is behaviour of a live Microsoft domain
controller (TLS certificates, AD-specific bind error codes); see docs/ACTIVE_DIRECTORY.md.
"""
import pytest
from conftest import admin_login
from ldap3 import MOCK_SYNC, OFFLINE_AD_2012_R2, Connection, Server

from app import accounts, db, directory, rbac

BASE = "DC=school,DC=local"
SVC = f"CN=svc-pager,OU=Service,{BASE}"


def group(name):
    return f"CN={name},OU=Groups,{BASE}"


@pytest.fixture
def ad(env):
    server = Server("dc1.school.local", get_info=OFFLINE_AD_2012_R2)
    seed = Connection(server, user=SVC, password="svc-pass", client_strategy=MOCK_SYNC)
    state = {"down": False, "server": server, "seed": seed}

    def add_user(sam, password, groups=(), uac=512, display=None):
        dn = f"CN={sam},OU=Staff,{BASE}"
        seed.strategy.add_entry(dn, {
            "objectClass": ["top", "person", "organizationalPerson", "user"], "objectCategory": "person",
            "sAMAccountName": sam, "userPrincipalName": f"{sam}@school.local", "distinguishedName": dn,
            "displayName": display or sam.title(), "mail": f"{sam}@school.local", "userPassword": password,
            "userAccountControl": uac, "memberOf": list(groups)})
        return dn

    def add_group(name, member_of=()):
        dn = group(name)
        seed.strategy.add_entry(dn, {"objectClass": ["top", "group"], "cn": name, "sAMAccountName": name,
                                     "distinguishedName": dn, "memberOf": list(member_of)})
        return dn

    seed.strategy.add_entry(SVC, {"objectClass": ["top", "person", "user"], "sAMAccountName": "svc-pager",
                                  "userPassword": "svc-pass", "distinguishedName": SVC})
    add_group("Pager Bells Admins")
    add_group("Grade Leaders", member_of=[group("Pager Bells Admins")])       # nested
    add_group("Pager Adhan Admins")
    add_group("Pager Full Admins")
    add_group("Unrelated")
    state["add_user"] = add_user

    def factory(cfg, user, password):
        if state["down"]:
            raise directory.DirectoryError("unavailable", "The directory server could not be reached.")
        c = Connection(server, user=user, password=password, client_strategy=MOCK_SYNC, raise_exceptions=False)
        if not c.bind():
            raise directory.DirectoryError("invalid_credentials", "invalid credentials")
        return c

    directory.set_connection_factory(factory)
    directory.save_config({"enabled": True, "server_urls": ["ldaps://dc1.school.local"], "security": "ldaps",
                           "bind_dn": SVC, "bind_password": "svc-pass", "base_dn": BASE,
                           "refresh_minutes": 15, "outage_grace_minutes": 60})
    directory.add_mapping(group("Pager Bells Admins"), "Pager Bells Admins", rbac.role_id_by_key("bells_admin"))
    directory.add_mapping(group("Pager Adhan Admins"), "Pager Adhan Admins", rbac.role_id_by_key("adhan_admin"))
    directory.add_mapping(group("Pager Full Admins"), "Pager Full Admins", rbac.role_id_by_key("full_admin"))
    return state


def test_bells_group_user_signs_in_through_nested_group(client, ad):
    ad["add_user"]("fatima", "Teach3r!", groups=[group("Grade Leaders")])
    r = client.post("/api/auth/login", json={"username": "SCHOOL\\fatima", "password": "Teach3r!"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user"]["source"] == "ad" and body["modules"] == ["bells"]
    assert "bells.schedule.edit" in body["permissions"] and "adhan.view" not in body["permissions"]
    assert any("AD group: Pager Bells Admins" in s for s in body["permission_sources"]["bells.view"])
    u = accounts.get_by_username("fatima")
    assert u["password_hash"] is None                               # domain password never stored
    assert client.get("/api/adhan/overview").status_code == 403


def test_upn_login_and_union_of_groups(client, ad):
    ad["add_user"]("omar", "Pa55word!", groups=[group("Pager Bells Admins"), group("Pager Adhan Admins")])
    r = client.post("/api/auth/login", json={"username": "omar@school.local", "password": "Pa55word!"})
    assert r.status_code == 200
    assert r.json()["modules"] == ["adhan", "bells"]


def test_full_admin_mapping(client, ad):
    ad["add_user"]("it", "Adm1n-pass", groups=[group("Pager Full Admins")])
    r = client.post("/api/auth/login", json={"username": "it", "password": "Adm1n-pass"})
    assert set(r.json()["permissions"]) == rbac.ALL_SET


def test_wrong_password_unmapped_and_disabled(client, ad):
    ad["add_user"]("ali", "Right-pass1", groups=[group("Unrelated")])
    ad["add_user"]("gone", "Right-pass1", groups=[group("Pager Bells Admins")], uac=514)
    ad["add_user"]("sara", "Right-pass1", groups=[group("Pager Bells Admins")])
    r = client.post("/api/auth/login", json={"username": "sara", "password": "wrong"})
    assert r.status_code == 401
    r = client.post("/api/auth/login", json={"username": "ali", "password": "Right-pass1"})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "not_authorized"
    r = client.post("/api/auth/login", json={"username": "gone", "password": "Right-pass1"})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "account_blocked"
    r = client.post("/api/auth/login", json={"username": "sara", "password": ""})
    assert r.status_code == 400                                     # empty password never binds
    assert accounts.get_by_username("ali") is None


def test_directory_outage(client, ad):
    ad["add_user"]("sara", "Right-pass1", groups=[group("Pager Bells Admins")])
    assert client.post("/api/auth/login", json={"username": "sara", "password": "Right-pass1"}).status_code == 200
    ad["down"] = True
    # within refresh interval: still works
    assert client.get("/api/bells/day").status_code == 200
    # stale but inside grace: works, refresh deferred
    db.execute("UPDATE users SET ad_synced_at = ? WHERE username = 'sara'", (db.now() - 20 * 60,))
    assert client.get("/api/bells/day").status_code == 200
    # beyond grace: refused until the directory answers again
    db.execute("UPDATE users SET ad_synced_at = ? WHERE username = 'sara'", (db.now() - 80 * 60,))
    r = client.get("/api/bells/day")
    assert r.status_code == 401 and r.json()["detail"]["code"] == "directory_unavailable"
    ad["down"] = False
    assert client.get("/api/bells/day").status_code == 200
    # new sign-ins during an outage fail clearly; local accounts still work
    ad["down"] = True
    client.cookies.clear()
    r = client.post("/api/auth/login", json={"username": "sara", "password": "Right-pass1"})
    assert r.status_code == 503 and r.json()["detail"]["code"] == "unavailable"
    admin_login(client)


def test_revoked_membership_and_disabled_account_on_refresh(client, ad):
    ad["add_user"]("sara", "Right-pass1", groups=[group("Pager Bells Admins")])
    assert client.post("/api/auth/login", json={"username": "sara", "password": "Right-pass1"}).status_code == 200
    ad["server"].dit[f"CN=sara,OU=Staff,{BASE}"]["memberOf"] = []
    user = accounts.get_by_username("sara")
    assert directory.refresh_user(user) == "revoked"
    assert client.get("/api/bells/day").status_code == 401
    assert db.one("SELECT COUNT(*) AS n FROM sessions WHERE user_id = ?", (user["id"],))["n"] == 0


def test_mapping_removal_takes_effect_immediately(client, ad):
    ad["add_user"]("sara", "Right-pass1", groups=[group("Pager Bells Admins"), group("Pager Adhan Admins")])
    client.post("/api/auth/login", json={"username": "sara", "password": "Right-pass1"})
    assert client.get("/api/adhan/overview").status_code == 200
    m = next(m for m in directory.list_mappings() if m["group_name"] == "Pager Adhan Admins")
    directory.delete_mapping(m["id"])
    assert client.get("/api/adhan/overview").status_code == 403
    assert client.get("/api/bells/day").status_code == 200


def test_local_account_name_takes_precedence(client, ad):
    ad["add_user"]("admin", "domain-pass", groups=[group("Pager Full Admins")])
    r = client.post("/api/auth/login", json={"username": "admin", "password": "domain-pass"})
    assert r.status_code == 401                                     # local recovery admin is not hijacked


def test_admin_api_hides_bind_password_and_tests(client, ad):
    h = admin_login(client)
    r = client.get("/api/directory")
    assert r.status_code == 200 and "svc-pass" not in r.text and r.json()["config"]["has_bind_password"]
    assert client.post("/api/directory/test", headers=h).json()["ok"] is True
    ad["add_user"]("fatima", "Teach3r!", groups=[group("Grade Leaders")])
    res = client.post("/api/directory/lookup-user", json={"username": "fatima"}, headers=h).json()
    assert res["would_sign_in"] and res["mapped_roles"] == ["School Bells Administrator"]
    assert {g["name"] for g in res["groups"]} == {"Grade Leaders", "Pager Bells Admins"}
    groups = client.get("/api/directory/groups?q=Pager").json()["groups"]
    assert {g["name"] for g in groups} >= {"Pager Bells Admins", "Pager Full Admins"}
    raw = db.one("SELECT value FROM settings WHERE key = 'directory'")["value"]
    assert "svc-pass" not in raw                                     # encrypted at rest
    assert "svc-pass" not in client.get("/api/audit").text


def test_config_validation(env):
    with pytest.raises(ValueError, match="ldaps://"):
        directory.save_config({"security": "ldaps", "server_urls": ["ldap://dc1"]})
    with pytest.raises(ValueError, match="fill in"):
        directory.save_config({"enabled": True, "security": "starttls", "server_urls": ["ldap://dc1"],
                               "bind_dn": "", "base_dn": BASE})


def test_mapping_escalation_guard(client, ad):
    from conftest import login, make_user
    make_user("diradmin", perms=["system.directory.manage", "bells.view"])
    h = login(client, "diradmin", "Correct-Horse-9")
    r = client.post("/api/directory/mappings", json={"group_dn": group("Unrelated"),
                                                     "role_id": rbac.role_id_by_key("full_admin")}, headers=h)
    assert r.status_code == 403
