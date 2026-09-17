"""HTTPS: settings and secrets, DNS providers (Cloudflare, cPanel), issuance job, renewal, TLS listener, redirect."""
import asyncio
import base64
import datetime
import json
import ssl
import time

import pytest
import requests
from conftest import login, make_user
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from app import accounts, audit, certificates, dns_providers, https_server


def admin_headers(client):
    accounts.set_password(accounts.get_by_username("admin")["id"], "Admin-Pass-123", must_change=False)
    return login(client, "admin", "Admin-Pass-123")


def self_signed(names, *, days=90, start=None):
    key = ec.generate_private_key(ec.SECP256R1())
    start = start or datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=5)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, names[0])])
    cert = (x509.CertificateBuilder().subject_name(subject)
            .issuer_name(x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Test CA"),
                                    x509.NameAttribute(NameOID.COMMON_NAME, "Test Issuer")]))
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(start).not_valid_after(start + datetime.timedelta(days=days))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(n) for n in names]), critical=False)
            .sign(key, hashes.SHA256()))
    return (cert.public_bytes(serialization.Encoding.PEM).decode(),
            key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption()).decode())


BASE = {"hostnames": ["pager.school-example.com"], "provider": "cloudflare", "cloudflare_token": "cf-secret-token-123",
        "agree_tos": True, "ca": "letsencrypt_staging"}


# --------------------------------------------------------------------------- #
# Settings, secrets, permissions
# --------------------------------------------------------------------------- #
def test_settings_validation(env):
    ok = certificates.merge_config({**BASE, "hostnames": ["Pager.School-Example.com.", "*.school-example.com",
                                                          "pager.school-example.com"]})
    assert ok["hostnames"] == ["pager.school-example.com", "*.school-example.com"]
    assert certificates.merge_config({**BASE, "hostnames": ["مدرسة.com"]})["hostnames"][0].startswith("xn--")
    for bad, text in ((["10.44.172.99"], "IP address"), (["pager.school.local"], "not a public domain"),
                      (["bad_name.com"], "not a valid host name"), (["x"] * 1, "not a valid host name")):
        with pytest.raises(ValueError, match=text):
            certificates.merge_config({**BASE, "hostnames": bad})
    with pytest.raises(ValueError, match="https://"):
        certificates.merge_config({**BASE, "provider": "cpanel", "cpanel_url": "http://cp.example.com:2083"})
    assert certificates.merge_config({**BASE, "cpanel_url": "cp.school-example.com:2083"})["cpanel_url"] == \
        "https://cp.school-example.com:2083"
    with pytest.raises(ValueError, match="DNS provider"):
        certificates.merge_config({**BASE, "provider": "google"})
    with pytest.raises(ValueError, match="email"):
        certificates.merge_config({**BASE, "email": "not-an-email"})
    with pytest.raises(ValueError, match="between 30 and 1800"):
        certificates.merge_config({**BASE, "dns_timeout_seconds": 5})


def test_tokens_encrypted_never_returned_or_audited(client):
    h = admin_headers(client)
    r = client.put("/api/system/https", json=BASE, headers=h)
    assert r.status_code == 200, r.text
    body = r.text
    assert "cf-secret-token-123" not in body and r.json()["config"]["has_cloudflare_token"] is True
    stored = json.dumps(certificates.get_config())
    assert "cf-secret-token-123" not in stored
    from app import security
    assert security.decrypt(certificates.get_config()["cloudflare_token_enc"]) == "cf-secret-token-123"
    # blank token keeps the saved one; clear removes it
    client.put("/api/system/https", json={**BASE, "cloudflare_token": ""}, headers=h)
    assert certificates.get_config()["cloudflare_token_enc"]
    entries = audit.search(action="https.update")["items"]
    assert entries and "cf-secret-token-123" not in json.dumps(entries)
    assert "cloudflare_token (secret)" in json.dumps(entries)
    client.put("/api/system/https", json={**BASE, "cloudflare_token": "", "clear_cloudflare_token": True}, headers=h)
    assert certificates.get_config()["cloudflare_token_enc"] == ""


def test_https_endpoints_need_certificate_permission(client):
    make_user("bellsboss", role_keys=["bells_admin"])
    make_user("sysadmin", perms=["system.settings.manage", "system.users.manage"])
    make_user("certadmin", perms=["system.certificates.manage"])
    calls = [("get", "/api/system/https", None), ("get", "/api/system/https/status", None),
             ("put", "/api/system/https", BASE), ("post", "/api/system/https/test", {}),
             ("post", "/api/system/https/request", None), ("delete", "/api/system/https/certificate", None)]
    for who in ("bellsboss", "sysadmin"):
        h = login(client, who, "Correct-Horse-9")
        for method, path, body in calls:
            kw = {"headers": h}
            if body is not None:
                kw["json"] = body
            assert getattr(client, method)(path, **kw).status_code == 403, (who, path)
    h = login(client, "certadmin", "Correct-Horse-9")
    assert client.get("/api/system/https", headers=h).status_code == 200


def test_request_refused_until_ready(client):
    h = admin_headers(client)
    r = client.post("/api/system/https/request", headers=h)
    assert r.status_code == 400 and "host name" in r.json()["detail"]["message"]
    client.put("/api/system/https", json={**BASE, "agree_tos": False}, headers=h)
    r = client.post("/api/system/https/request", headers=h)
    assert r.status_code == 400 and "Subscriber Agreement" in r.json()["detail"]["message"]


# --------------------------------------------------------------------------- #
# DNS providers (HTTP APIs faked)
# --------------------------------------------------------------------------- #
class FakeResponse:
    def __init__(self, status=200, body=None, text=None):
        self.status_code = status
        self._body = body
        self._text = text

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class FakeSession:
    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        return self.handler(method, url, kw)

    def get(self, url, **kw):
        return self.request("GET", url, **kw)

    def post(self, url, **kw):
        return self.request("POST", url, **kw)


def test_cloudflare_zone_lookup_add_remove():
    def handler(method, url, kw):
        assert kw["headers"]["Authorization"] == "Bearer tok"
        if url.endswith("/zones") and method == "GET":
            name = kw["params"].get("name")
            if name is None:
                return FakeResponse(body={"success": True, "result": [{"name": "school-example.com", "id": "z1"}]})
            return FakeResponse(body={"success": True, "result": [{"name": name, "id": "z1"}]
                                      if name == "school-example.com" else []})
        if url.endswith("/zones/z1/dns_records") and method == "POST":
            return FakeResponse(body={"success": True, "result": {"id": "rec9"}})
        if url.endswith("/zones/z1/dns_records/rec9") and method == "DELETE":
            return FakeResponse(body={"success": True, "result": {"id": "rec9"}})
        raise AssertionError(url)

    s = FakeSession(handler)
    cf = dns_providers.Cloudflare("tok", session=s)
    assert cf.check() == ["school-example.com"]
    rec = cf.add_txt("_acme-challenge.pager.school-example.com", "abc123")
    assert (rec.zone, rec.zone_id, rec.record_id) == ("school-example.com", "z1", "rec9")
    post = [c for c in s.calls if c[0] == "POST"][0]
    assert post[2]["json"] == {"type": "TXT", "name": "_acme-challenge.pager.school-example.com",
                               "content": '"abc123"', "ttl": 120}
    cf.remove_txt(rec)
    assert s.calls[-1][0] == "DELETE"


def test_cloudflare_errors():
    cf = dns_providers.Cloudflare("bad", session=FakeSession(lambda m, u, k: FakeResponse(
        403, {"success": False, "errors": [{"code": 9109, "message": "Invalid access token"}]})))
    with pytest.raises(dns_providers.DnsError) as e:
        cf.check()
    assert e.value.code == "auth" and "Invalid access token" in e.value.message and "bad" not in e.value.message

    cf = dns_providers.Cloudflare("tok", session=FakeSession(lambda m, u, k: FakeResponse(
        body={"success": True, "result": []})))
    with pytest.raises(dns_providers.DnsError) as e:
        cf.find_zone("_acme-challenge.pager.other-example.org")
    assert e.value.code == "zone_not_found"

    def boom(m, u, k):
        raise requests.exceptions.ConnectionError("down")
    with pytest.raises(dns_providers.DnsError) as e:
        dns_providers.Cloudflare("tok", session=FakeSession(boom)).check()
    assert e.value.code == "unreachable"
    with pytest.raises(dns_providers.DnsError, match="token"):
        dns_providers.Cloudflare("")


def _b64(s):
    return base64.b64encode(s.encode()).decode()


class FakeCPanel:
    """Minimal cPanel UAPI zone model: DomainInfo/list_domains, DNS/parse_zone, DNS/mass_edit_zone."""

    def __init__(self):
        self.serial = 2026091701
        self.records = []          # (line, name, [values])
        self.next_line = 20
        self.stale_serial_rejections = 0

    def handler(self, method, url, kw):
        assert kw["headers"]["Authorization"] == "cpanel schooluser:cp-token"
        assert kw["verify"] is True
        fn = url.split("/execute/")[1]
        p = kw["params"]
        if fn == "DomainInfo/list_domains":
            return FakeResponse(body={"status": 1, "data": {"main_domain": "school-example.com",
                                                            "addon_domains": ["other-example.org"],
                                                            "parked_domains": [], "sub_domains": ["pager.school-example.com"]}})
        if fn == "DNS/parse_zone":
            assert p["zone"] == "school-example.com"
            data = [{"type": "record", "record_type": "SOA", "line_index": 1, "dname_b64": _b64("school-example.com."),
                     "data_b64": [_b64("ns1.host.com."), _b64("admin.host.com."), _b64(str(self.serial)),
                                  _b64("3600"), _b64("1800"), _b64("1209600"), _b64("86400")]},
                    {"type": "comment", "text_b64": _b64("; zone")}]
            data += [{"type": "record", "record_type": "TXT", "line_index": line, "dname_b64": _b64(name),
                      "data_b64": [_b64(v) for v in values]} for line, name, values in self.records]
            return FakeResponse(body={"status": 1, "data": data})
        if fn == "DNS/mass_edit_zone":
            if int(p["serial"]) != self.serial:
                self.stale_serial_rejections += 1
                return FakeResponse(body={"status": 0, "errors": ["The serial number does not match."]})
            if "add" in p:
                rec = json.loads(p["add"])
                assert rec["record_type"] == "TXT" and rec["dname"].endswith(".")
                self.records.append((self.next_line, rec["dname"], rec["data"]))
                self.next_line += 1
            elif "remove" in p:
                self.records = [r for r in self.records if r[0] != int(p["remove"])]
            elif "edit" in p:
                rec = json.loads(p["edit"])
                self.records = [(line, n, rec["data"] if line == rec["line_index"] else v) for line, n, v in self.records]
            self.serial += 1
            return FakeResponse(body={"status": 1, "data": {"new_serial": str(self.serial)}})
        raise AssertionError(fn)


def test_cpanel_add_and_remove_records():
    fake = FakeCPanel()
    cp = dns_providers.CPanel("https://cp.host.com:2083/", "schooluser", "cp-token", session=FakeSession(fake.handler))
    assert cp.check() == ["other-example.org", "school-example.com"]
    a = cp.add_txt("_acme-challenge.pager.school-example.com", "value-one")
    b = cp.add_txt("_acme-challenge.pager.school-example.com", "value-two")
    assert a.zone == "school-example.com"
    assert [r[1:] for r in fake.records] == [("_acme-challenge.pager.school-example.com.", ["value-one"]),
                                             ("_acme-challenge.pager.school-example.com.", ["value-two"])]
    cp.remove_txt(a)
    assert [r[2] for r in fake.records] == [["value-two"]]
    # a record holding several values is edited, not deleted
    fake.records = [(30, "_acme-challenge.pager.school-example.com.", ["keep-me", "value-two"])]
    cp.remove_txt(b)
    assert fake.records == [(30, "_acme-challenge.pager.school-example.com.", ["keep-me"])]
    assert fake.stale_serial_rejections == 0
    with pytest.raises(dns_providers.DnsError) as e:
        cp.find_zone("_acme-challenge.pager.not-mine-example.net")
    assert e.value.code == "zone_not_found"


def test_cpanel_errors():
    cp = dns_providers.CPanel("https://cp.host.com:2083", "u", "t",
                              session=FakeSession(lambda m, u, k: FakeResponse(401)))
    with pytest.raises(dns_providers.DnsError) as e:
        cp.check()
    assert e.value.code == "auth"
    cp = dns_providers.CPanel("https://cp.host.com:2083", "u", "t",
                              session=FakeSession(lambda m, u, k: FakeResponse(200, None, "<html>login</html>")))
    with pytest.raises(dns_providers.DnsError, match="API response"):
        cp.check()
    cp = dns_providers.CPanel("https://cp.host.com:2083", "u", "t", session=FakeSession(
        lambda m, u, k: FakeResponse(body={"status": 0, "errors": ["Access denied to DomainInfo"]})))
    with pytest.raises(dns_providers.DnsError) as e:
        cp.check()
    assert e.value.code == "auth"

    def tls(m, u, k):
        raise requests.exceptions.SSLError("bad cert")
    with pytest.raises(dns_providers.DnsError) as e:
        dns_providers.CPanel("https://cp.host.com:2083", "u", "t", session=FakeSession(tls)).check()
    assert e.value.code == "tls_error"


def test_provider_test_endpoint_reports_zones_and_write_test(client, monkeypatch):
    h = admin_headers(client)
    log = []

    class P(dns_providers.Provider):
        def check(self):
            return ["school-example.com"]

        def find_zone(self, fqdn):
            if not fqdn.endswith("school-example.com"):
                raise dns_providers.DnsError("zone_not_found", "nope")
            return "school-example.com", "z"

        def add_txt(self, fqdn, value):
            log.append(("add", fqdn))
            return dns_providers.TxtRecord(fqdn, value, "school-example.com")

        def remove_txt(self, rec):
            log.append(("remove", rec.fqdn))

    certificates.set_provider_factory(lambda cfg: P())
    try:
        r = client.post("/api/system/https/test", json={**BASE, "write_test": True}, headers=h).json()
        assert r["ok"] and r["write_tested"] and r["zones"] == [{"hostname": "pager.school-example.com",
                                                                 "zone": "school-example.com"}]
        assert log == [("add", "_acme-challenge.pager.school-example.com"),
                       ("remove", "_acme-challenge.pager.school-example.com")]
        r = client.post("/api/system/https/test", json={**BASE, "hostnames": ["pager.other-example.net"]}, headers=h).json()
        assert not r["ok"] and r["code"] == "zone_not_found"
        # unsaved values are not stored by a test
        assert certificates.get_config()["hostnames"] == []
    finally:
        certificates.set_provider_factory(None)


# --------------------------------------------------------------------------- #
# DNS propagation wait
# --------------------------------------------------------------------------- #
def test_wait_for_txt(monkeypatch):
    rec = dns_providers.TxtRecord("_acme-challenge.pager.school-example.com", "v1", "school-example.com")
    provider = dns_providers.Provider()
    provider.fallback_wait = 42
    seen = {"n": 0}
    monkeypatch.setattr(certificates, "_authoritative_servers", lambda zone: ["192.0.2.1", "192.0.2.2"])

    def values(server, fqdn):
        seen["n"] += 1
        return {"v1"} if seen["n"] > 3 else set()
    monkeypatch.setattr(certificates, "_txt_values", values)
    sleeps = []
    certificates.wait_for_txt([rec], provider, timeout=600, sleep=sleeps.append)
    assert sleeps == [10, 10, 10, 5]

    monkeypatch.setattr(certificates, "_txt_values", lambda s, f: set())
    clock = iter([0, 0, 1000])
    monkeypatch.setattr(certificates.time, "monotonic", lambda: next(clock))
    with pytest.raises(certificates.CertError) as e:
        certificates.wait_for_txt([rec], provider, timeout=60, sleep=lambda s: None)
    assert e.value.code == "dns" and "name servers" in e.value.message

    def no_ns(zone):
        raise OSError("no network")
    monkeypatch.setattr(certificates, "_authoritative_servers", no_ns)
    sleeps = []
    certificates.wait_for_txt([rec], provider, timeout=60, sleep=sleeps.append)
    assert sleeps == [42]


# --------------------------------------------------------------------------- #
# Issuance job, status, renewal
# --------------------------------------------------------------------------- #
def ready_config():
    certificates.save_config(BASE)


def test_successful_request_installs_certificate(env):
    ready_config()
    fullchain, key = self_signed(["pager.school-example.com"])

    def fake_issue(cfg, job):
        job.step("Checking the DNS provider")
        job.step("Adding the DNS record {name}", name="_acme-challenge.pager.school-example.com")
        return fullchain, key

    certificates.set_acme_factory(fake_issue)
    try:
        job = certificates.request_certificate("admin", wait=True)
    finally:
        certificates.set_acme_factory(None)
    job = certificates.current_job()
    assert job["ok"] and not job["running"] and all(s["state"] == "done" for s in job["steps"])
    state = certificates.get_state()
    assert state["certificate"]["names"] == ["pager.school-example.com"]
    assert key not in json.dumps(state)                            # private key stored encrypted
    certfile, keyfile = certificates.cert_paths()
    assert open(certfile).read() == fullchain and open(keyfile).read() == key
    st = certificates.status({"running": False, "port": 8443, "error": ""})
    assert st["certificate"]["covers_hostnames"] and st["certificate"]["days_left"] in (89, 90)
    assert st["certificate"]["test_ca"] is True                    # staging CA in BASE
    assert audit.search(action="https.certificate_issued")["items"]


def test_failed_request_backs_off_and_recovery_is_audited(env):
    ready_config()
    calls = {"n": 0}
    fullchain, key = self_signed(["pager.school-example.com"])

    def flaky(cfg, job):
        calls["n"] += 1
        job.step("Checking the DNS provider")
        if calls["n"] <= 2:
            raise dns_providers.DnsError("auth", "Cloudflare refused the API token (Invalid access token).")
        return fullchain, key

    certificates.set_acme_factory(flaky)
    try:
        certificates.request_certificate("admin", wait=True)
        st = certificates.get_state()
        assert st["failures"] == 1 and st["last_error"]["code"] == "auth"
        assert 3500 <= st["next_attempt_at"] - time.time() <= 3601
        job = certificates.current_job()
        assert job["ok"] is False and job["steps"][-1]["state"] == "failed"
        certificates.request_certificate("admin", wait=True)
        assert 7100 <= certificates.get_state()["next_attempt_at"] - time.time() <= 7201
        certificates.request_certificate("admin", wait=True)
    finally:
        certificates.set_acme_factory(None)
    st = certificates.get_state()
    assert st["failures"] == 0 and st["last_error"] is None and st["certificate"]
    failed = audit.search(action="https.certificate_failed")["items"]
    assert len(failed) == 2 and failed[0]["result"] == "error"
    assert audit.search(action="https.renewal_recovered")["items"]


def test_mismatched_key_is_refused(env):
    ready_config()
    fullchain, _ = self_signed(["pager.school-example.com"])
    _, other_key = self_signed(["pager.school-example.com"])
    with pytest.raises(certificates.CertError, match="does not match"):
        certificates.install_certificate(fullchain, other_key, ca="letsencrypt")
    assert "certificate" not in certificates.get_state()


def test_renewal_schedule(env, monkeypatch):
    ready_config()
    certificates.save_config({"enabled": True})
    start = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=10)
    fullchain, key = self_signed(["pager.school-example.com"], start=start)
    certificates.install_certificate(fullchain, key, ca="letsencrypt")
    cert = certificates.get_state()["certificate"]
    assert certificates.renew_at(cert) == cert["not_before"] + 60 * 86400
    now = time.time()
    assert not certificates.renewal_due(now)
    assert certificates.renewal_due(cert["not_before"] + 61 * 86400)
    state = certificates.get_state()
    state["next_attempt_at"] = cert["not_before"] + 62 * 86400
    certificates._save_state(state)
    assert not certificates.renewal_due(cert["not_before"] + 61 * 86400)          # backing off
    assert certificates.renewal_due(cert["not_before"] + 63 * 86400)
    certificates.save_config({"enabled": False})
    assert not certificates.renewal_due(cert["not_before"] + 63 * 86400)          # HTTPS off: no renewal

    certificates.save_config({"enabled": True})
    state["next_attempt_at"] = None
    certificates._save_state(state)
    started = []
    monkeypatch.setattr(certificates, "renewal_due", lambda now=None: True)
    monkeypatch.setattr(certificates, "request_certificate",
                        lambda actor, reason="manual", wait=False: started.append((actor, reason, wait)))
    assert certificates.check_renewal() and started == [("system", "renewal", True)]


def test_status_states(env):
    ready_config()
    listener = {"running": True, "port": 8443, "error": ""}
    assert certificates.status(listener)["state"] == "off"
    certificates.save_config({"enabled": True})
    assert certificates.status(listener)["state"] == "no_certificate"
    now = datetime.datetime.now(datetime.timezone.utc)
    # a short-lived (6-day) certificate is fine early in its life
    fullchain, key = self_signed(["pager.school-example.com"], days=6)
    certificates.install_certificate(fullchain, key, ca="letsencrypt")
    assert certificates.status(listener)["state"] == "active"
    # 90-day certificate with 10 days left: renewal overdue -> expiring
    fullchain, key = self_signed(["pager.school-example.com"], days=90, start=now - datetime.timedelta(days=80))
    certificates.install_certificate(fullchain, key, ca="letsencrypt")
    assert certificates.status(listener)["state"] == "expiring"
    # past its renewal time with failed attempts -> renewal failing
    fullchain, key = self_signed(["pager.school-example.com"], days=90, start=now - datetime.timedelta(days=65))
    certificates.install_certificate(fullchain, key, ca="letsencrypt")
    state = certificates.get_state()
    state["failures"] = 2
    certificates._save_state(state)
    assert certificates.status(listener)["state"] == "renewal_failing"
    state["failures"] = 0
    certificates._save_state(state)
    fullchain, key = self_signed(["pager.school-example.com"], days=90)
    certificates.install_certificate(fullchain, key, ca="letsencrypt")
    assert certificates.status(listener)["state"] == "active"
    assert certificates.status({**listener, "error": "Could not listen"})["state"] == "listener_error"
    certificates.save_config({"hostnames": ["pager.school-example.com", "bells.school-example.com"]})
    assert certificates.status(listener)["certificate"]["covers_hostnames"] is False


def test_remove_certificate_endpoint(client):
    h = admin_headers(client)
    client.put("/api/system/https", json=BASE, headers=h)
    assert client.delete("/api/system/https/certificate", headers=h).status_code == 404
    fullchain, key = self_signed(["pager.school-example.com"])
    certificates.install_certificate(fullchain, key, ca="letsencrypt")
    r = client.delete("/api/system/https/certificate", headers=h)
    assert r.status_code == 200 and r.json()["status"]["certificate"] is None
    certfile, keyfile = certificates.cert_paths()
    import os
    assert not os.path.exists(certfile) and not os.path.exists(keyfile)
    assert audit.search(action="https.certificate_remove")["items"]


def test_dashboard_shows_https_health(client):
    h = admin_headers(client)
    assert "https" not in client.get("/api/dashboard", headers=h).json()["health"]
    client.put("/api/system/https", json={**BASE, "enabled": True}, headers=h)
    cell = client.get("/api/dashboard", headers=h).json()["health"]["https"]
    assert cell["ok"] is False and cell["state"] == "no_certificate"


# --------------------------------------------------------------------------- #
# TLS listener with live certificate reload
# --------------------------------------------------------------------------- #
async def _hello_app(scope, receive, send):
    if scope["type"] != "http":
        return
    body = f"scheme={scope['scheme']}".encode()
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


def test_listener_serves_tls_and_reloads_renewed_certificate(env):
    ready_config()
    certificates.save_config({"enabled": True})
    first = self_signed(["pager.school-example.com"])
    second = self_signed(["pager.school-example.com"])
    certificates.install_certificate(*first, ca="letsencrypt")

    async def fetch(port):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        reader, writer = await asyncio.open_connection("127.0.0.1", port, ssl=ctx, server_hostname="pager.school-example.com")
        der = writer.get_extra_info("ssl_object").getpeercert(binary_form=True)
        writer.write(b"GET / HTTP/1.1\r\nHost: pager.school-example.com\r\nConnection: close\r\n\r\n")
        await writer.drain()
        data = await reader.read()
        writer.close()
        return x509.load_der_x509_certificate(der).serial_number, data

    async def scenario():
        listener = https_server.HttpsListener(_hello_app, host="127.0.0.1", port=0)
        listener.attach(asyncio.get_running_loop())
        try:
            await listener.apply()
            assert listener.running and not listener.error
            for _ in range(50):
                if listener.server and listener.server.started:
                    break
                await asyncio.sleep(0.05)
            serial1, data = await fetch(listener.port)
            assert data.endswith(b"scheme=https")
            certificates.install_certificate(*second, ca="letsencrypt")      # hook schedules apply()
            await asyncio.sleep(0.2)
            await listener.apply()
            serial2, _ = await fetch(listener.port)
            assert serial1 != serial2
            assert serial2 == x509.load_pem_x509_certificate(second[0].encode()).serial_number
            certificates.save_config({"enabled": False})
            await asyncio.sleep(0.2)
            await listener.apply()
            assert not listener.running
        finally:
            await listener.stop()
            certificates.set_listener_hook(None)

    asyncio.run(scenario())


def test_listener_reports_port_in_use(env):
    import socket
    ready_config()
    certificates.save_config({"enabled": True})
    certificates.install_certificate(*self_signed(["pager.school-example.com"]), ca="letsencrypt")
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    port = blocker.getsockname()[1]

    async def scenario():
        listener = https_server.HttpsListener(_hello_app, host="127.0.0.1", port=port)
        try:
            await listener.apply()
            assert not listener.running and f"port {port}" in listener.error
            assert certificates.status(listener.status())["state"] == "listener_error"
        finally:
            await listener.stop()
    try:
        asyncio.run(scenario())
    finally:
        blocker.close()


# --------------------------------------------------------------------------- #
# HTTP -> HTTPS redirect and cookies
# --------------------------------------------------------------------------- #
class _RunningListener:
    running = True
    port = 8443

    def status(self):
        return {"running": True, "port": 8443, "error": ""}


def test_redirect_only_when_enabled_and_serving(client, monkeypatch):
    h = admin_headers(client)
    client.put("/api/system/https", json={**BASE, "enabled": True, "redirect_http": False}, headers=h)
    monkeypatch.setattr(https_server, "_listener", _RunningListener())
    certificates._invalidate_cache()
    assert client.get("/", headers={"host": "pager.school-example.com"}, follow_redirects=False).status_code == 200
    client.put("/api/system/https", json={**BASE, "enabled": True, "redirect_http": True}, headers=h)
    certificates._invalidate_cache()
    r = client.get("/bells?x=1", headers={"host": "pager.school-example.com:8090"}, follow_redirects=False)
    assert r.status_code == 308 and r.headers["location"] == "https://pager.school-example.com:8443/bells?x=1"
    # opened by IP address: sent to the certificate's name
    r = client.get("/", headers={"host": "10.44.172.99:8090"}, follow_redirects=False)
    assert r.headers["location"] == "https://pager.school-example.com:8443/"
    # health checks are never redirected
    assert client.get("/api/health", follow_redirects=False).status_code in (200, 503)
    # nothing is redirected while HTTPS is not actually being served
    monkeypatch.setattr(https_server, "_listener", None)
    assert client.get("/", follow_redirects=False).status_code == 200


def test_cookies_are_secure_over_https(env):
    from conftest import _noop_lifespan
    from fastapi.testclient import TestClient

    from app import main
    main.app.router.lifespan_context = _noop_lifespan
    accounts.set_password(accounts.get_by_username("admin")["id"], "Admin-Pass-123", must_change=False)
    with TestClient(main.app, base_url="https://testserver") as c:
        r = c.post("/api/auth/login", json={"username": "admin", "password": "Admin-Pass-123"})
        cookies = r.headers.get_list("set-cookie")
        assert cookies and all("Secure" in x for x in cookies)
    with TestClient(main.app, base_url="http://testserver") as c:
        r = c.post("/api/auth/login", json={"username": "admin", "password": "Admin-Pass-123"})
        assert all("Secure" not in x for x in r.headers.get_list("set-cookie"))


def test_cli_disable_https(env):
    from app import cli
    certificates.save_config({**BASE, "enabled": True, "redirect_http": True})
    assert cli.disable_https(None) == 0
    cfg = certificates.get_config()
    assert cfg["enabled"] is False and cfg["redirect_http"] is False and cfg["cloudflare_token_enc"]
