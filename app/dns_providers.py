"""DNS providers for Let's Encrypt DNS-01 validation: Cloudflare and cPanel.

Each provider can check its credentials, find the DNS zone that holds a name, and
add or remove the temporary ``_acme-challenge`` TXT records. API tokens are
passed in by ``certificates`` (decrypted only for the call) and are never logged.
"""
from __future__ import annotations

import base64
import json
import logging
import os
from dataclasses import dataclass

import requests

log = logging.getLogger("certificates")

HTTP_TIMEOUT = 30
USER_AGENT = "adhan-bell-pager"


class DnsError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class TxtRecord:
    fqdn: str            # _acme-challenge.pager.example.com (no trailing dot)
    value: str
    zone: str            # example.com
    zone_id: str = ""
    record_id: str = ""


def parent_names(fqdn: str) -> list[str]:
    """``a.b.example.com`` -> ``[a.b.example.com, b.example.com, example.com]`` (at least two labels)."""
    labels = fqdn.rstrip(".").lower().split(".")
    return [".".join(labels[i:]) for i in range(len(labels) - 1)]


def _network_error(provider: str, exc: Exception) -> DnsError:
    if isinstance(exc, requests.exceptions.SSLError):
        return DnsError("tls_error", f"The secure connection to {provider} was not trusted. Check the address "
                                     "and its certificate.")
    if isinstance(exc, requests.exceptions.Timeout):
        return DnsError("unreachable", f"{provider} did not answer in time. Check this server's internet access.")
    return DnsError("unreachable", f"Could not reach {provider}. Check the address and this server's internet "
                                   "access (outbound HTTPS).")


class Provider:
    key = ""
    label = ""
    ttl = 120
    fallback_wait = 60          # seconds to wait when DNS cannot be checked directly

    def check(self) -> list[str]:
        """Verify credentials; return the zone names the credentials can manage."""
        raise NotImplementedError

    def find_zone(self, fqdn: str) -> tuple[str, str]:
        raise NotImplementedError

    def add_txt(self, fqdn: str, value: str) -> TxtRecord:
        raise NotImplementedError

    def remove_txt(self, rec: TxtRecord) -> None:
        raise NotImplementedError

    check_propagation = True


# --------------------------------------------------------------------------- #
# Cloudflare (API v4, scoped API token)
# --------------------------------------------------------------------------- #
class Cloudflare(Provider):
    key = "cloudflare"
    label = "Cloudflare"
    ttl = 120
    fallback_wait = 30
    API = "https://api.cloudflare.com/client/v4"

    def __init__(self, api_token: str, session: requests.Session | None = None):
        if not api_token:
            raise DnsError("missing_credentials", "Enter the Cloudflare API token.")
        self._token = api_token
        self.session = session or requests.Session()

    def _call(self, method: str, path: str, **kw) -> dict:
        try:
            r = self.session.request(method, self.API + path, timeout=HTTP_TIMEOUT,
                                     headers={"Authorization": f"Bearer {self._token}",
                                              "Content-Type": "application/json", "User-Agent": USER_AGENT}, **kw)
        except requests.exceptions.RequestException as e:
            raise _network_error("Cloudflare", e) from None
        try:
            body = r.json()
        except ValueError:
            body = {}
        if r.status_code < 400 and body.get("success"):
            return body
        errors = body.get("errors") or []
        msg = "; ".join(str(e.get("message", "")) for e in errors if isinstance(e, dict)) or f"HTTP {r.status_code}"
        codes = {e.get("code") for e in errors if isinstance(e, dict)}
        if r.status_code in (401, 403) or codes & {9109, 10000, 6003, 6111}:
            raise DnsError("auth", f"Cloudflare refused the API token ({msg}). Create a token with Zone → DNS → Edit "
                                   "and Zone → Zone → Read for your domain.")
        raise DnsError("api_error", f"Cloudflare returned an error: {msg}")

    def check(self) -> list[str]:
        body = self._call("GET", "/zones", params={"per_page": 50, "status": "active"})
        zones = sorted(z.get("name", "") for z in body.get("result") or [])
        if not zones:
            raise DnsError("no_zones", "The token works but cannot see any active zone. Give it Zone → Zone → Read "
                                       "and Zone → DNS → Edit for your domain.")
        return zones

    def find_zone(self, fqdn: str) -> tuple[str, str]:
        for name in parent_names(fqdn):
            body = self._call("GET", "/zones", params={"name": name})
            result = body.get("result") or []
            if result:
                return result[0]["name"], result[0]["id"]
        raise DnsError("zone_not_found", f"No Cloudflare zone that contains {fqdn.removeprefix('_acme-challenge.')} "
                                         "is visible to this token.")

    def add_txt(self, fqdn: str, value: str) -> TxtRecord:
        zone, zone_id = self.find_zone(fqdn)
        body = self._call("POST", f"/zones/{zone_id}/dns_records",
                          json={"type": "TXT", "name": fqdn, "content": f'"{value}"', "ttl": self.ttl})
        return TxtRecord(fqdn=fqdn, value=value, zone=zone, zone_id=zone_id, record_id=body["result"]["id"])

    def remove_txt(self, rec: TxtRecord) -> None:
        self._call("DELETE", f"/zones/{rec.zone_id}/dns_records/{rec.record_id}")


# --------------------------------------------------------------------------- #
# cPanel (UAPI with an API token; the domain's DNS must be hosted by this cPanel)
# --------------------------------------------------------------------------- #
def _b64(s: str) -> str:
    return base64.b64encode(s.encode("utf-8")).decode("ascii")


def _unb64(s: str) -> str:
    try:
        return base64.b64decode(s).decode("utf-8", "replace")
    except (ValueError, TypeError):
        return ""


class CPanel(Provider):
    key = "cpanel"
    label = "cPanel"
    ttl = 300
    fallback_wait = 90

    def __init__(self, base_url: str, username: str, api_token: str, *, verify_tls: bool = True,
                 session: requests.Session | None = None):
        if not base_url or not username or not api_token:
            raise DnsError("missing_credentials", "Enter the cPanel address, username and API token.")
        self.base_url = base_url.rstrip("/")
        self.username = username
        self._token = api_token
        self.verify_tls = verify_tls
        self.session = session or requests.Session()

    def _call(self, function: str, **params):
        try:
            r = self.session.get(f"{self.base_url}/execute/{function}", params=params, timeout=HTTP_TIMEOUT,
                                 verify=self.verify_tls,
                                 headers={"Authorization": f"cpanel {self.username}:{self._token}",
                                          "Accept": "application/json", "User-Agent": USER_AGENT})
        except requests.exceptions.RequestException as e:
            raise _network_error("cPanel", e) from None
        if r.status_code in (401, 403):
            raise DnsError("auth", "cPanel refused the username or API token.")
        if r.status_code != 200:
            raise DnsError("api_error", f"cPanel answered HTTP {r.status_code}. Check the address "
                                        "(usually https://your-cpanel-server:2083).")
        try:
            body = r.json()
        except ValueError:
            raise DnsError("api_error", "cPanel did not return an API response. Check the address "
                                        "(usually https://your-cpanel-server:2083).") from None
        if isinstance(body.get("result"), dict):
            body = body["result"]
        if not body.get("status"):
            errors = body.get("errors") or ["the request failed"]
            text = "; ".join(str(e) for e in errors)
            if "access" in text.lower() and "denied" in text.lower():
                raise DnsError("auth", f"cPanel denied access: {text}")
            raise DnsError("api_error", f"cPanel returned an error: {text}")
        return body.get("data")

    def _zones(self) -> list[str]:
        data = self._call("DomainInfo/list_domains") or {}
        zones = [data.get("main_domain") or ""]
        zones += list(data.get("addon_domains") or []) + list(data.get("parked_domains") or [])
        return sorted({z.lower() for z in zones if z})

    def check(self) -> list[str]:
        zones = self._zones()
        if not zones:
            raise DnsError("no_zones", "This cPanel account has no domains.")
        return zones

    def find_zone(self, fqdn: str) -> tuple[str, str]:
        zones = set(self._zones())
        for name in parent_names(fqdn):
            if name in zones:
                return name, name
        raise DnsError("zone_not_found", f"No domain in this cPanel account contains "
                                         f"{fqdn.removeprefix('_acme-challenge.')}.")

    def _zone_records(self, zone: str) -> tuple[list[dict], int]:
        records = self._call("DNS/parse_zone", zone=zone) or []
        for r in records:
            if r.get("type") == "record" and r.get("record_type") == "SOA":
                if _unb64(r.get("dname_b64", "")).rstrip(".").lower() == zone:
                    try:
                        return records, int(_unb64(r["data_b64"][2]).strip())
                    except (KeyError, IndexError, ValueError):
                        break
        raise DnsError("api_error", f"cPanel did not return the zone serial for {zone}. Is its DNS hosted in this "
                                    "cPanel account?")

    def add_txt(self, fqdn: str, value: str) -> TxtRecord:
        zone, _ = self.find_zone(fqdn)
        _, serial = self._zone_records(zone)
        record = {"dname": fqdn + ".", "ttl": self.ttl, "record_type": "TXT", "data": [value]}
        self._call("DNS/mass_edit_zone", zone=zone, serial=serial, add=json.dumps(record))
        return TxtRecord(fqdn=fqdn, value=value, zone=zone, zone_id=zone)

    def remove_txt(self, rec: TxtRecord) -> None:
        target = _b64(rec.value)
        for _ in range(5):
            records, serial = self._zone_records(rec.zone)
            match = None
            for r in records:
                if r.get("type") != "record" or r.get("record_type") != "TXT":
                    continue
                name = _unb64(r.get("dname_b64", "")).rstrip(".").lower()
                if name not in (rec.fqdn, rec.fqdn.removesuffix("." + rec.zone)):
                    continue
                if target in (r.get("data_b64") or []):
                    match = r
                    break
            if match is None:
                return
            rest = [_unb64(d) for d in match.get("data_b64") or [] if d != target]
            if rest:
                edit = {"line_index": match["line_index"], "dname": rec.fqdn + ".", "ttl": self.ttl,
                        "record_type": "TXT", "data": rest}
                self._call("DNS/mass_edit_zone", zone=rec.zone, serial=serial, edit=json.dumps(edit))
            else:
                self._call("DNS/mass_edit_zone", zone=rec.zone, serial=serial, remove=match["line_index"])


# --------------------------------------------------------------------------- #
# Test provider: Pebble's challenge test server (only when ACME_TEST_DNS_URL is set)
# --------------------------------------------------------------------------- #
class ChallTestSrv(Provider):
    key = "pebble_challtestsrv"
    label = "Pebble challenge test server"
    fallback_wait = 0
    check_propagation = False

    def __init__(self, url: str, session: requests.Session | None = None):
        self.url = url.rstrip("/")
        self.session = session or requests.Session()

    def _post(self, path: str, body: dict) -> None:
        try:
            r = self.session.post(self.url + path, json=body, timeout=HTTP_TIMEOUT)
        except requests.exceptions.RequestException as e:
            raise _network_error("the DNS test server", e) from None
        if r.status_code != 200:
            raise DnsError("api_error", f"DNS test server answered HTTP {r.status_code}.")

    def check(self) -> list[str]:
        return ["(any)"]

    def find_zone(self, fqdn: str) -> tuple[str, str]:
        return parent_names(fqdn)[-1], ""

    def add_txt(self, fqdn: str, value: str) -> TxtRecord:
        self._post("/set-txt", {"host": fqdn + ".", "value": value})
        return TxtRecord(fqdn=fqdn, value=value, zone=self.find_zone(fqdn)[0])

    def remove_txt(self, rec: TxtRecord) -> None:
        self._post("/clear-txt", {"host": rec.fqdn + "."})


def challtestsrv_url() -> str:
    return os.environ.get("ACME_TEST_DNS_URL", "").strip()


PROVIDERS = {"cloudflare": "Cloudflare", "cpanel": "cPanel"}


def available() -> list[dict]:
    out = [{"key": k, "label": v} for k, v in PROVIDERS.items()]
    if challtestsrv_url():
        out.append({"key": ChallTestSrv.key, "label": ChallTestSrv.label})
    return out
