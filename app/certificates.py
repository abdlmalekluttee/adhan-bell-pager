"""HTTPS certificates from Let's Encrypt, validated through DNS (DNS-01).

Design
------
* DNS validation means Let's Encrypt never connects to this server: it only checks a
  temporary TXT record (``_acme-challenge.<name>``) that we publish through the DNS
  provider's API (Cloudflare or cPanel). The server can stay on a private network.
* Before asking Let's Encrypt to validate, we wait until the record is visible on the
  domain's authoritative name servers, so validation does not fail on slow DNS.
* The certificate and its private key are stored in the database (key encrypted with
  the app secret key), so backups include them. They are written to ``<config>/tls``
  for the HTTPS listener.
* **Renewal:** checked every 30 minutes; a certificate is renewed once two thirds of its
  lifetime has passed (day 60 of 90). Failures retry with back-off (1 h, 2 h, 4 h … 12 h)
  and are shown on the dashboard and recorded in the audit log.
* API tokens are encrypted at rest, never returned by the API, never logged.
"""
from __future__ import annotations

import datetime
import logging
import os
import re
import ssl
import tempfile
import threading
import time
import urllib.parse
from dataclasses import dataclass, field

from . import __version__, audit, config, db, dns_providers, security
from .dns_providers import DnsError

log = logging.getLogger("certificates")

SETTINGS_KEY = "https"
STATE_KEY = "https_state"
CA_DIRECTORIES = {
    "letsencrypt": "https://acme-v02.api.letsencrypt.org/directory",
    "letsencrypt_staging": "https://acme-staging-v02.api.letsencrypt.org/directory",
}
TOS_URL = "https://letsencrypt.org/repository/"
DEFAULTS = {
    "enabled": False,                  # serve the app over HTTPS once a certificate exists
    "hostnames": [],                   # e.g. ["pager.school.example"]
    "email": "",
    "provider": "cloudflare",          # cloudflare | cpanel
    "cloudflare_token_enc": "",
    "cpanel_url": "",
    "cpanel_username": "",
    "cpanel_token_enc": "",
    "cpanel_verify_tls": True,
    "ca": "letsencrypt",               # letsencrypt | letsencrypt_staging | custom
    "directory_url": "",               # only for ca == custom
    "key_type": "ecdsa",               # ecdsa | rsa
    "redirect_http": False,
    "agree_tos": False,
    "dns_timeout_seconds": 300,
}
SECRET_FIELDS = ("cloudflare_token_enc", "cpanel_token_enc")
CHECK_INTERVAL = 1800
RENEW_FRACTION = 2 / 3
MAX_BACKOFF = 12 * 3600

_HOST_RE = re.compile(r"^(\*\.)?([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+(xn--[a-z0-9-]{1,59}|[a-z]{2,63})$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PRIVATE_SUFFIXES = (".local", ".lan", ".internal", ".localdomain", ".home.arpa", ".corp", ".intranet", ".test",
                     ".invalid", ".example", ".localhost")

_lock = threading.RLock()
_job: Job | None = None
_listener_hook = None                  # called (no args) after certificates or settings change
_provider_factory = None               # tests inject providers
_acme_factory = None                   # tests inject a fake ACME flow
_stop = threading.Event()
_thread: threading.Thread | None = None
_cfg_cache: tuple[float, dict] | None = None


class CertError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
def get_config() -> dict:
    cfg = dict(DEFAULTS)
    cfg.update(db.get_setting(SETTINGS_KEY, {}) or {})
    return cfg


def cached_config(max_age: float = 5.0) -> dict:
    """Settings for the per-request redirect check, re-read at most every few seconds."""
    global _cfg_cache
    now = time.monotonic()
    if _cfg_cache is None or now - _cfg_cache[0] > max_age:
        try:
            _cfg_cache = (now, get_config())
        except Exception:  # noqa: BLE001
            return dict(DEFAULTS)
    return _cfg_cache[1]


def _invalidate_cache() -> None:
    global _cfg_cache
    _cfg_cache = None


def public_config(cfg: dict | None = None) -> dict:
    cfg = dict(cfg or get_config())
    cfg["has_cloudflare_token"] = bool(cfg.pop("cloudflare_token_enc", ""))
    cfg["has_cpanel_token"] = bool(cfg.pop("cpanel_token_enc", ""))
    return cfg


def normalize_hostname(name: str) -> str:
    name = (name or "").strip().rstrip(".").lower()
    if not name:
        return ""
    try:
        wildcard = name.startswith("*.")
        base = name[2:] if wildcard else name
        base = base.encode("idna").decode("ascii")
        name = ("*." if wildcard else "") + base
    except UnicodeError:
        raise ValueError(f"“{name}” is not a valid host name.") from None
    return name


def _validate(cfg: dict) -> dict:
    names = cfg.get("hostnames") or []
    if isinstance(names, str):
        names = re.split(r"[\s,]+", names)
    out: list[str] = []
    for raw in names:
        n = normalize_hostname(str(raw))
        if not n:
            continue
        if re.fullmatch(r"[\d.]+|[0-9a-f:]+", n):
            raise ValueError(f"“{n}” is an IP address. Let's Encrypt certificates need a domain name.")
        if not _HOST_RE.match(n) or len(n) > 253:
            raise ValueError(f"“{n}” is not a valid host name.")
        if n.endswith(_PRIVATE_SUFFIXES):
            raise ValueError(f"“{n}” is not a public domain. Let's Encrypt only issues certificates for names in a "
                             "public DNS domain that you control.")
        if n not in out:
            out.append(n)
    if len(out) > 10:
        raise ValueError("Use at most 10 host names.")
    cfg["hostnames"] = out
    cfg["email"] = str(cfg.get("email") or "").strip()
    if cfg["email"] and not _EMAIL_RE.match(cfg["email"]):
        raise ValueError("Enter a valid email address, or leave it blank.")
    valid_providers = set(dns_providers.PROVIDERS)
    if dns_providers.challtestsrv_url():
        valid_providers.add(dns_providers.ChallTestSrv.key)
    if cfg.get("provider") not in valid_providers:
        raise ValueError("Choose Cloudflare or cPanel as the DNS provider.")
    url = str(cfg.get("cpanel_url") or "").strip().rstrip("/")
    if url and not re.match(r"^[a-z][a-z0-9+.-]*://", url, re.I):
        url = "https://" + url
    if url and not url.lower().startswith("https://"):
        raise ValueError("The cPanel address must use https:// so the API token is never sent unencrypted.")
    cfg["cpanel_url"] = url
    cfg["cpanel_username"] = str(cfg.get("cpanel_username") or "").strip()
    cfg["cpanel_verify_tls"] = bool(cfg.get("cpanel_verify_tls", True))
    if cfg.get("ca") not in (*CA_DIRECTORIES, "custom"):
        raise ValueError("Choose a certificate authority.")
    cfg["directory_url"] = str(cfg.get("directory_url") or "").strip()
    if cfg["ca"] == "custom" and not cfg["directory_url"].lower().startswith("https://"):
        raise ValueError("The ACME directory address must start with https://.")
    if cfg.get("key_type") not in ("ecdsa", "rsa"):
        raise ValueError("Choose ECDSA or RSA for the key type.")
    for k in ("enabled", "redirect_http", "agree_tos"):
        cfg[k] = bool(cfg.get(k))
    try:
        cfg["dns_timeout_seconds"] = int(cfg.get("dns_timeout_seconds") or DEFAULTS["dns_timeout_seconds"])
    except (TypeError, ValueError):
        raise ValueError("DNS wait must be a number of seconds.") from None
    if not 30 <= cfg["dns_timeout_seconds"] <= 1800:
        raise ValueError("DNS wait must be between 30 and 1800 seconds.")
    return cfg


def merge_config(data: dict, base: dict | None = None) -> dict:
    """Apply submitted values (plain tokens are encrypted; blank tokens keep the saved ones)."""
    cfg = dict(base or get_config())
    for key in DEFAULTS:
        if key in SECRET_FIELDS or key not in data:
            continue
        cfg[key] = data[key]
    for plain, enc, clear in (("cloudflare_token", "cloudflare_token_enc", "clear_cloudflare_token"),
                              ("cpanel_token", "cpanel_token_enc", "clear_cpanel_token")):
        if data.get(clear):
            cfg[enc] = ""
        elif data.get(plain):
            cfg[enc] = security.encrypt(str(data[plain]).strip())
    return _validate(cfg)


def save_config(data: dict) -> dict:
    with _lock:
        before = get_config()
        cfg = merge_config(data, before)
        db.set_setting(SETTINGS_KEY, cfg)
        _invalidate_cache()
    changed = sorted(k for k in cfg if cfg.get(k) != before.get(k))
    _notify_listener()
    return {"config": public_config(cfg),
            "changed": [c.replace("_enc", "") + " (secret)" if c in SECRET_FIELDS else c for c in changed]}


def provider_for(cfg: dict) -> dns_providers.Provider:
    if _provider_factory:
        return _provider_factory(cfg)
    p = cfg.get("provider")
    if p == "cloudflare":
        return dns_providers.Cloudflare(security.decrypt(cfg.get("cloudflare_token_enc", "")))
    if p == "cpanel":
        return dns_providers.CPanel(cfg.get("cpanel_url", ""), cfg.get("cpanel_username", ""),
                                    security.decrypt(cfg.get("cpanel_token_enc", "")),
                                    verify_tls=cfg.get("cpanel_verify_tls", True))
    if p == dns_providers.ChallTestSrv.key and dns_providers.challtestsrv_url():
        return dns_providers.ChallTestSrv(dns_providers.challtestsrv_url())
    raise DnsError("missing_credentials", "Choose a DNS provider.")


def set_provider_factory(fn) -> None:
    global _provider_factory
    _provider_factory = fn


def set_acme_factory(fn) -> None:
    global _acme_factory
    _acme_factory = fn


def check_provider(data: dict | None = None, *, write_test: bool = False) -> dict:
    """Check DNS provider credentials (unsaved values allowed) and find the zone for each host name."""
    try:
        cfg = merge_config(data or {}) if data else get_config()
    except ValueError as e:
        return {"ok": False, "code": "invalid", "message": str(e), "zones": []}
    try:
        provider = provider_for(cfg)
        visible = provider.check()
        zones = []
        for host in cfg["hostnames"]:
            fqdn = "_acme-challenge." + host.removeprefix("*.")
            zone, _ = provider.find_zone(fqdn)
            zones.append({"hostname": host, "zone": zone})
        written = False
        if write_test and cfg["hostnames"]:
            fqdn = "_acme-challenge." + cfg["hostnames"][0].removeprefix("*.")
            rec = provider.add_txt(fqdn, "adhan-bell-pager-test-" + security.new_token()[:12])
            provider.remove_txt(rec)
            written = True
    except DnsError as e:
        return {"ok": False, "code": e.code, "message": e.message, "zones": []}
    msg = ("The DNS provider accepted the credentials and a test record was added and removed." if written
           else "The DNS provider accepted the credentials.")
    return {"ok": True, "code": "ok", "message": msg, "zones": zones, "visible_zones": visible[:20],
            "write_tested": written}


# --------------------------------------------------------------------------- #
# Stored certificate
# --------------------------------------------------------------------------- #
def tls_dir() -> str:
    return os.path.join(os.path.dirname(config.CONFIG_PATH), "tls")


def cert_paths() -> tuple[str, str]:
    d = tls_dir()
    return os.path.join(d, "fullchain.pem"), os.path.join(d, "privkey.pem")


def get_state() -> dict:
    return db.get_setting(STATE_KEY, {}) or {}


def _save_state(state: dict) -> None:
    db.set_setting(STATE_KEY, state)


def parse_certificate(fullchain_pem: str) -> dict:
    from cryptography import x509
    from cryptography.x509.oid import ExtensionOID, NameOID

    certs = x509.load_pem_x509_certificates(fullchain_pem.encode("ascii"))
    if not certs:
        raise CertError("invalid_certificate", "The certificate could not be read.")
    c = certs[0]
    try:
        san = c.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME).value
        names = [n.lower() for n in san.get_values_for_type(x509.DNSName)]
    except x509.ExtensionNotFound:
        names = []

    def _name(n, oid):
        attrs = n.get_attributes_for_oid(oid)
        return str(attrs[0].value) if attrs else ""

    issuer = " ".join(x for x in (_name(c.issuer, NameOID.ORGANIZATION_NAME), _name(c.issuer, NameOID.COMMON_NAME)) if x)
    return {"names": names, "not_before": int(c.not_valid_before_utc.timestamp()),
            "not_after": int(c.not_valid_after_utc.timestamp()), "issuer": issuer or c.issuer.rfc4514_string(),
            "serial": format(c.serial_number, "x")}


def _write_private(path: str, data: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="ascii") as fh:
            fh.write(data)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def materialize() -> bool:
    """Write the stored certificate to <config>/tls for the listener. Returns True if one exists."""
    cert = get_state().get("certificate")
    certfile, keyfile = cert_paths()
    if not cert:
        for p in (certfile, keyfile):
            if os.path.exists(p):
                os.unlink(p)
        return False
    key = security.decrypt(cert.get("key_enc", ""))
    if not key:
        log.error("certificates: the stored private key could not be decrypted (secret key changed?)")
        return False
    _write_private(keyfile, key)
    _write_private(certfile, cert["fullchain_pem"])
    return True


def _check_pair(fullchain_pem: str, key_pem: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        c, k = os.path.join(tmp, "c.pem"), os.path.join(tmp, "k.pem")
        with open(c, "w", encoding="ascii") as fh:
            fh.write(fullchain_pem)
        with open(k, "w", encoding="ascii") as fh:
            fh.write(key_pem)
        try:
            ssl.create_default_context(ssl.Purpose.CLIENT_AUTH).load_cert_chain(c, k)
        except ssl.SSLError as e:
            raise CertError("invalid_certificate", f"The certificate does not match its key ({e.reason}).") from None


def install_certificate(fullchain_pem: str, key_pem: str, *, ca: str) -> dict:
    info = parse_certificate(fullchain_pem)
    _check_pair(fullchain_pem, key_pem)
    with _lock:
        state = get_state()
        state["certificate"] = {**info, "fullchain_pem": fullchain_pem, "key_enc": security.encrypt(key_pem),
                                "ca": ca, "installed_at": db.now()}
        _save_state(state)
        materialize()
    _notify_listener()
    return info


def remove_certificate() -> None:
    with _lock:
        state = get_state()
        state.pop("certificate", None)
        state["failures"] = 0
        state["next_attempt_at"] = None
        state["last_error"] = None
        _save_state(state)
        materialize()
    _notify_listener()


def has_certificate() -> bool:
    cert = get_state().get("certificate")
    return bool(cert) and cert.get("not_after", 0) > time.time()


def set_listener_hook(fn) -> None:
    global _listener_hook
    _listener_hook = fn


def _notify_listener() -> None:
    if _listener_hook:
        try:
            _listener_hook()
        except Exception:  # noqa: BLE001
            log.exception("certificates: could not apply HTTPS listener changes")


def renew_at(cert: dict) -> int:
    return int(cert["not_before"] + (cert["not_after"] - cert["not_before"]) * RENEW_FRACTION)


def _host_matches(host: str, names: list[str]) -> bool:
    host = host.lower()
    for n in names:
        if n == host or (n.startswith("*.") and host.count(".") == n.count(".") and host.endswith(n[1:])):
            return True
    return False


# --------------------------------------------------------------------------- #
# Issuance job (runs in a background thread; progress is polled by the UI)
# --------------------------------------------------------------------------- #
@dataclass
class Job:
    actor: str
    reason: str                        # manual | renewal
    started_at: int = field(default_factory=db.now)
    finished_at: int | None = None
    ok: bool | None = None
    error: dict | None = None
    steps: list = field(default_factory=list)

    def step(self, text: str, **params) -> None:
        with _lock:
            for s in self.steps:
                if s["state"] == "running":
                    s["state"] = "done"
            self.steps.append({"at": db.now(), "text": text, "params": params, "state": "running"})
        log.info("certificates: " + text.format(**params))

    def public(self) -> dict:
        with _lock:
            return {"actor": self.actor, "reason": self.reason, "started_at": self.started_at,
                    "finished_at": self.finished_at, "running": self.finished_at is None, "ok": self.ok,
                    "error": self.error, "steps": [dict(s) for s in self.steps]}


def current_job() -> dict | None:
    return _job.public() if _job else None


def request_certificate(actor: str, *, reason: str = "manual", wait: bool = False) -> dict:
    """Start issuing a certificate for the saved settings. Raises CertError if not ready or already running."""
    global _job
    cfg = get_config()
    problems = readiness(cfg)
    if problems:
        raise CertError("not_ready", problems[0])
    with _lock:
        if _job and _job.finished_at is None:
            raise CertError("busy", "A certificate request is already running.")
        job = Job(actor=actor, reason=reason)
        _job = job
    t = threading.Thread(target=_run_job, args=(job, cfg), name="certificate-request", daemon=True)
    t.start()
    if wait:
        t.join()
    return job.public()


def readiness(cfg: dict) -> list[str]:
    problems = []
    if not cfg["hostnames"]:
        problems.append("Add at least one host name.")
    if not cfg["agree_tos"]:
        problems.append("Accept the Let's Encrypt Subscriber Agreement first.")
    if cfg["provider"] == "cloudflare" and not cfg["cloudflare_token_enc"]:
        problems.append("Save a Cloudflare API token first.")
    if cfg["provider"] == "cpanel" and not (cfg["cpanel_url"] and cfg["cpanel_username"] and cfg["cpanel_token_enc"]):
        problems.append("Save the cPanel address, username and API token first.")
    return problems


def _run_job(job: Job, cfg: dict) -> None:
    state = get_state()
    state["last_attempt_at"] = db.now()
    _save_state(state)
    try:
        issue = _acme_factory or issue_certificate
        fullchain, key_pem = issue(cfg, job)
        job.step("Installing the certificate")
        info = install_certificate(fullchain, key_pem, ca=cfg["ca"])
    except Exception as e:  # noqa: BLE001
        code, message = describe_error(e)
        with _lock:
            for s in job.steps:
                if s["state"] == "running":
                    s["state"] = "failed"
            job.ok, job.error, job.finished_at = False, {"code": code, "message": message}, db.now()
            state = get_state()
            state["failures"] = int(state.get("failures") or 0) + 1
            state["last_error"] = {"code": code, "message": message, "at": db.now()}
            delay = min(MAX_BACKOFF, 3600 * 2 ** (state["failures"] - 1))
            state["next_attempt_at"] = db.now() + delay
            _save_state(state)
        log.warning("certificates: request failed: %s", message)
        audit.record("https.certificate_failed", actor=job.actor, target=",".join(cfg["hostnames"]), result="error",
                     details={"reason": job.reason, "code": code, "error": message, "failures": state["failures"]})
        return
    with _lock:
        for s in job.steps:
            s["state"] = "done"
        job.ok, job.finished_at = True, db.now()
        state = get_state()
        recovered = int(state.get("failures") or 0) > 0
        state.update(failures=0, last_error=None, next_attempt_at=None, last_success_at=db.now())
        _save_state(state)
    details = {"reason": job.reason, "names": info["names"], "expires": _iso(info["not_after"]), "ca": cfg["ca"]}
    audit.record("https.certificate_issued", actor=job.actor, target=",".join(info["names"]), details=details)
    if recovered:
        audit.record("https.renewal_recovered", actor="system", target=",".join(info["names"]))
    log.info("certificates: installed certificate for %s, valid until %s", ", ".join(info["names"]),
             _iso(info["not_after"]))


def _iso(ts: int) -> str:
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).isoformat()


def describe_error(e: Exception) -> tuple[str, str]:
    if isinstance(e, (CertError, DnsError)):
        return e.code, e.message
    try:
        import requests
        from acme import errors as acme_errors
        from acme import messages
    except ImportError:  # pragma: no cover
        return "error", str(e)
    if isinstance(e, acme_errors.ValidationError):
        details = []
        for authzr in e.failed_authzrs:
            for ch in authzr.body.challenges:
                if ch.error:
                    details.append(f"{authzr.body.identifier.value}: {ch.error.detail or ch.error.typ}")
        return "validation_failed", ("Let's Encrypt could not verify the DNS record. "
                                     + ("; ".join(details) if details else "Check the DNS zone and try again."))
    if isinstance(e, messages.Error):
        typ = (e.typ or "").rsplit(":", 1)[-1]
        if typ == "rateLimited":
            return "rate_limited", f"Let's Encrypt rate limit reached: {e.detail}. Wait before trying again, or use " \
                                   "the staging service for tests."
        if typ in ("rejectedIdentifier", "caa"):
            return "rejected", f"Let's Encrypt will not issue for this name: {e.detail}"
        return "acme_error", f"Let's Encrypt returned an error: {e.detail or e.typ}"
    if isinstance(e, acme_errors.TimeoutError):
        return "timeout", "Let's Encrypt did not finish in time. Try again in a few minutes."
    if isinstance(e, requests.exceptions.SSLError):
        return "tls_error", "The connection to the certificate authority was not trusted."
    if isinstance(e, requests.exceptions.RequestException):
        return "unreachable", "Could not reach the certificate authority. Check this server's internet access " \
                              "(outbound HTTPS)."
    log.exception("certificates: unexpected error", exc_info=e)
    return "error", f"The certificate request failed: {e}"


def directory_url(cfg: dict) -> str:
    return cfg["directory_url"] if cfg["ca"] == "custom" else CA_DIRECTORIES[cfg["ca"]]


def _account_key(url: str):
    """ACME account key per certificate authority, created once and kept encrypted."""
    import josepy as jose
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    with _lock:
        state = get_state()
        accounts = state.setdefault("accounts", {})
        pem = security.decrypt((accounts.get(url) or {}).get("key_enc", ""))
        if pem:
            key = serialization.load_pem_private_key(pem.encode("ascii"), password=None)
        else:
            key = ec.generate_private_key(ec.SECP256R1())
            pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption()).decode("ascii")
            accounts[url] = {"key_enc": security.encrypt(pem), "created_at": db.now()}
            _save_state(state)
    return jose.JWKEC(key=key)


def _new_private_key(key_type: str) -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec, rsa

    key = ec.generate_private_key(ec.SECP256R1()) if key_type == "ecdsa" else \
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption()).decode("ascii")


def issue_certificate(cfg: dict, job: Job) -> tuple[str, str]:
    """Run the ACME DNS-01 flow. Returns (fullchain PEM, private key PEM)."""
    import josepy as jose
    from acme import challenges, client, crypto_util, messages
    from acme import errors as acme_errors

    provider = provider_for(cfg)
    url = directory_url(cfg)
    ca_label = {"letsencrypt": "Let's Encrypt", "letsencrypt_staging": "Let's Encrypt (staging)"}.get(
        cfg["ca"], urllib.parse.urlsplit(url).hostname or url)
    job.step("Checking the DNS provider")
    provider.check()

    job.step("Connecting to {ca}", ca=ca_label)
    jwk = _account_key(url)
    verify = os.environ.get("ACME_CA_BUNDLE") or True
    net = client.ClientNetwork(jwk, alg=jose.ES256, verify_ssl=verify, timeout=45,
                               user_agent=f"adhan-bell-pager/{__version__}")
    directory = client.ClientV2.get_directory(url, net)
    acme = client.ClientV2(directory, net=net)
    reg = messages.NewRegistration.from_data(email=cfg["email"] or None, terms_of_service_agreed=True)
    try:
        acme.new_account(reg)
    except acme_errors.ConflictError as e:
        net.account = messages.RegistrationResource(uri=e.location, body=messages.Registration())

    job.step("Creating a new private key")
    key_pem = _new_private_key(cfg["key_type"])
    csr = crypto_util.make_csr(key_pem.encode("ascii"), cfg["hostnames"])
    job.step("Creating the certificate order")
    order = acme.new_order(csr)

    published: list[dns_providers.TxtRecord] = []
    try:
        answers = []
        for authz in order.authorizations:
            if authz.body.status == messages.STATUS_VALID:
                continue
            domain = authz.body.identifier.value
            chall = next((c for c in authz.body.challenges if isinstance(c.chall, challenges.DNS01)), None)
            if chall is None:
                raise CertError("no_dns_challenge", f"The certificate authority did not offer DNS validation for {domain}.")
            fqdn = chall.chall.validation_domain_name(domain)
            value = chall.chall.validation(jwk)
            job.step("Adding the DNS record {name}", name=fqdn)
            published.append(provider.add_txt(fqdn, value))
            answers.append(chall)
        if published and provider.check_propagation:
            job.step("Waiting for the DNS record to appear on the domain's name servers")
            wait_for_txt(published, provider, timeout=cfg["dns_timeout_seconds"])
        job.step("{ca} is checking the DNS records", ca=ca_label)
        for chall in answers:
            acme.answer_challenge(chall, chall.chall.response(jwk))
        deadline = datetime.datetime.now() + datetime.timedelta(seconds=180)
        order = acme.poll_authorizations(order, deadline)
        job.step("Downloading the certificate")
        order = acme.finalize_order(order, deadline)
    finally:
        if published:
            job.step("Removing the temporary DNS records")
            for rec in published:
                try:
                    provider.remove_txt(rec)
                except DnsError as e:
                    log.warning("certificates: could not remove %s: %s", rec.fqdn, e.message)
    return order.fullchain_pem, key_pem


# --------------------------------------------------------------------------- #
# DNS propagation check
# --------------------------------------------------------------------------- #
PUBLIC_RESOLVERS = ["1.1.1.1", "8.8.8.8", "9.9.9.9"]


def _authoritative_servers(zone: str) -> list[str]:
    import dns.resolver

    res = dns.resolver.Resolver(configure=False)
    res.nameservers = PUBLIC_RESOLVERS
    res.lifetime = 8
    ips: list[str] = []
    for ns in res.resolve(zone, "NS"):
        host = str(ns.target).rstrip(".")
        for rdtype in ("A", "AAAA"):
            try:
                ips += [str(a) for a in res.resolve(host, rdtype)]
            except Exception:  # noqa: BLE001
                continue
    return sorted(set(ips))


def _txt_values(server: str, fqdn: str) -> set[str]:
    import dns.exception
    import dns.flags
    import dns.message
    import dns.query
    import dns.rdatatype

    q = dns.message.make_query(fqdn, dns.rdatatype.TXT)
    try:
        resp = dns.query.udp(q, server, timeout=4)
        if resp.flags & dns.flags.TC:
            resp = dns.query.tcp(q, server, timeout=6)
    except (dns.exception.DNSException, OSError):
        return set()
    out = set()
    for rrset in resp.answer:
        if rrset.rdtype == dns.rdatatype.TXT:
            for rd in rrset:
                out.add(b"".join(rd.strings).decode("ascii", "replace"))
    return out


def wait_for_txt(records: list[dns_providers.TxtRecord], provider: dns_providers.Provider, *, timeout: int,
                 sleep=time.sleep) -> None:
    if not provider.check_propagation:
        return
    try:
        servers = {z: _authoritative_servers(z) for z in {r.zone for r in records}}
        if not all(servers.values()):
            raise RuntimeError("no name servers found")
    except Exception as e:  # noqa: BLE001
        log.info("certificates: cannot check DNS directly (%s); waiting %s s instead", e, provider.fallback_wait)
        sleep(provider.fallback_wait)
        return
    deadline = time.monotonic() + timeout
    while True:
        missing = [r for r in records if any(r.value not in _txt_values(s, r.fqdn) for s in servers[r.zone])]
        if not missing:
            sleep(5)            # let secondary servers settle
            return
        if time.monotonic() >= deadline:
            names = ", ".join(sorted({r.fqdn for r in missing}))
            raise CertError("dns", f"The DNS record did not appear on the domain's name servers within {timeout} "
                                   f"seconds ({names}). Check that the domain's DNS is really hosted by the selected "
                                   "provider, or increase the DNS wait.")
        sleep(10)


# --------------------------------------------------------------------------- #
# Status and automatic renewal
# --------------------------------------------------------------------------- #
def status(listener: dict | None = None) -> dict:
    cfg = get_config()
    state = get_state()
    now = int(time.time())
    cert = state.get("certificate")
    cert_out = None
    if cert:
        cert_out = {k: cert[k] for k in ("names", "not_before", "not_after", "issuer", "serial", "ca", "installed_at")
                    if k in cert}
        cert_out["days_left"] = int((cert["not_after"] - now) // 86400)
        cert_out["expired"] = cert["not_after"] <= now
        cert_out["renew_at"] = renew_at(cert)
        cert_out["test_ca"] = cert.get("ca") != "letsencrypt"
        cert_out["covers_hostnames"] = bool(cfg["hostnames"]) and all(
            h in cert["names"] or _host_matches(h, cert["names"]) for h in cfg["hostnames"])
    listener = listener or {}
    failures = int(state.get("failures") or 0)
    lifetime = (cert["not_after"] - cert["not_before"]) if cert else 0
    remaining = (cert["not_after"] - now) if cert else 0
    if not cfg["enabled"]:
        overall = "off"
    elif not cert:
        overall = "no_certificate"
    elif cert_out["expired"]:
        overall = "expired"
    elif listener.get("error"):
        overall = "listener_error"
    elif remaining < lifetime / 6:          # renewal was due at one third remaining and has not happened
        overall = "expiring"
    elif failures and now >= renew_at(cert):
        overall = "renewal_failing"
    else:
        overall = "active"
    return {
        "state": overall,
        "ready_problems": readiness(cfg),
        "certificate": cert_out,
        "renewal": {"last_attempt_at": state.get("last_attempt_at"), "last_success_at": state.get("last_success_at"),
                    "last_error": state.get("last_error"), "failures": failures,
                    "next_attempt_at": state.get("next_attempt_at")},
        "job": current_job(),
        "listener": listener,
    }


def renewal_due(now: int | None = None) -> bool:
    now = int(now or time.time())
    cfg = get_config()
    state = get_state()
    cert = state.get("certificate")
    if not cfg["enabled"] or not cert or readiness(cfg):
        return False
    if state.get("next_attempt_at") and now < state["next_attempt_at"]:
        return False
    return now >= renew_at(cert)


def check_renewal() -> bool:
    if _job and _job.finished_at is None:
        return False
    if not renewal_due():
        return False
    try:
        request_certificate("system", reason="renewal", wait=True)
    except CertError as e:
        log.warning("certificates: renewal not started: %s", e.message)
        return False
    return True


def _loop() -> None:
    if _stop.wait(60):
        return
    while not _stop.is_set():
        try:
            check_renewal()
        except Exception:  # noqa: BLE001
            log.exception("certificates: renewal check failed")
        _stop.wait(CHECK_INTERVAL)


def start() -> None:
    global _thread
    try:
        materialize()
    except Exception:  # noqa: BLE001
        log.exception("certificates: could not write the stored certificate")
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="certificate-renewal", daemon=True)
    _thread.start()


def stop() -> None:
    _stop.set()


def redirect_enabled() -> bool:
    cfg = cached_config()
    return bool(cfg.get("enabled") and cfg.get("redirect_http"))


def redirect_host(request_host: str) -> str:
    """Host to redirect to: the requested name if the certificate covers it, else the first configured name."""
    cfg = cached_config()
    names = [h for h in cfg.get("hostnames") or []]
    host = (request_host or "").split(":")[0].strip("[]").lower()
    if host and _host_matches(host, names):
        return host
    for n in names:
        if not n.startswith("*."):
            return n
    return host
